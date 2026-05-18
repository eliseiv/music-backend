from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import (
    BeatNotFound,
    FalProviderError,
    FalTimeout,
    JobForbidden,
    JobNotFound,
    TrackNotFound,
)
from app.config import Settings
from app.music.enums import JobStage, JobStatus
from app.music.models import Beat, Job, JobStageLog, Track
from app.music.providers.fal.base import FalProvider, FalWebhookEvent
from app.music.repositories.jobs import JobsRepository
from app.music.repositories.tracks import TracksRepository
from app.music.services.pipeline import Pipeline
from app.music.services.pricing_service import PricingService
from app.music.services.subscription_gate import SubscriptionGate
from app.music.services.url_validator import validate_urls_reachable
from app.music.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


@dataclass
class CreateJobResult:
    job_id: UUID
    status: JobStatus
    tokens_reserved: int


class GenerationService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        fal: FalProvider,
        wallet: WalletService,
        pricing: PricingService,
        gate: SubscriptionGate,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fal = fal
        self._wallet = wallet
        self._pricing = pricing
        self._gate = gate
        self._settings = settings
        self._pipeline = Pipeline(sessionmaker, fal, wallet, pricing, settings)

    async def create_job(
        self,
        *,
        user_id: UUID,
        request_payload: dict[str, Any],
        store_stems: bool,
        desired_duration_seconds: int | None,
        client_idempotency_key: str | None = None,
    ) -> CreateJobResult:
        # 1. Подписка
        await self._gate.ensure_active(user_id)

        # 2. Beat существует
        async with self._sessionmaker() as session:
            beat = await session.get(Beat, request_payload.get("beat_id"))
            if beat is None or not beat.active:
                raise BeatNotFound()

        # 3. Idempotency-Key — возможно job уже создан
        if client_idempotency_key:
            async with self._sessionmaker() as session:
                repo = JobsRepository(session)
                existing = await repo.get_by_idempotency_key(
                    user_id=user_id, key=client_idempotency_key
                )
            if existing is not None:
                logger.info(
                    "Idempotent generate: returning existing job=%s for key=%s",
                    existing.id,
                    client_idempotency_key,
                )
                return CreateJobResult(
                    job_id=existing.id,
                    status=existing.status,
                    tokens_reserved=existing.reserved_tokens,
                )

        # 4. URL HEAD-проверка
        urls = _collect_urls(request_payload)
        await validate_urls_reachable(
            urls,
            timeout_seconds=self._settings.MUSIC_URL_CHECK_TIMEOUT_SECONDS,
            enabled=self._settings.MUSIC_URL_CHECK_ENABLED,
        )

        # 5. Pricing
        rule = await self._pricing.resolve_active_rule(
            provider_model=self._settings.FAL_MUSIC_MODEL
        )
        reserved_tokens = self._pricing.required_tokens_for_precharge(
            rule, requested_duration_seconds=desired_duration_seconds
        )

        # Сохраняем desired_duration в payload, чтобы Pipeline мог использовать
        request_payload = dict(request_payload)
        if desired_duration_seconds is not None:
            request_payload["desired_duration_seconds"] = desired_duration_seconds

        # 6. Создаём job + резерв токенов
        async with self._sessionmaker() as session:
            async with session.begin():
                jobs = JobsRepository(session)
                job = await jobs.add(
                    user_id=user_id,
                    provider_model=self._settings.FAL_MUSIC_MODEL,
                    pricing_rule_id=rule.id,
                    reserved_tokens=reserved_tokens,
                    input_payload=request_payload,
                    store_stems=store_stems,
                    client_idempotency_key=client_idempotency_key,
                )
                job_id = job.id
        await self._wallet.reserve(
            user_id=user_id,
            amount=reserved_tokens,
            ref_type="job",
            ref_id=str(job_id),
        )

        # 7. Запускаем пайплайн (start выполняет prepare_prompt + lyrics inline
        # и сабмитит первый async stage music_generation)
        try:
            await self._pipeline.start(job_id)
        except (FalProviderError, FalTimeout) as exc:
            # Откатываем job + возвращаем токены
            await self._wallet.release(
                user_id=user_id,
                amount=reserved_tokens,
                ref_type="job",
                ref_id=str(job_id),
            )
            async with self._sessionmaker() as session:
                async with session.begin():
                    jobs = JobsRepository(session)
                    await jobs.mark_failed(
                        job_id=job_id,
                        error_code=exc.code,
                        error_message=exc.message,
                    )
            raise

        return CreateJobResult(
            job_id=job_id,
            status=JobStatus.processing,
            tokens_reserved=reserved_tokens,
        )

    async def get_job(self, *, user_id: UUID, job_id: UUID) -> Job:
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise JobNotFound()
            if job.user_id != user_id:
                raise JobForbidden()
            session.expunge(job)
            return job

    async def get_track(self, *, user_id: UUID, track_id: UUID) -> Track:
        async with self._sessionmaker() as session:
            track = await session.get(Track, track_id)
            if track is None:
                raise TrackNotFound()
            if track.user_id != user_id:
                raise JobForbidden()
            session.expunge(track)
            return track

    async def get_track_for_job(
        self, *, user_id: UUID, job_id: UUID
    ) -> Track | None:
        async with self._sessionmaker() as session:
            repo = TracksRepository(session)
            track = await repo.get_by_job_id(job_id)
            if track is None:
                return None
            if track.user_id != user_id:
                raise JobForbidden()
            session.expunge(track)
            return track

    async def list_stage_events(
        self, *, user_id: UUID, job_id: UUID
    ) -> list[JobStageLog]:
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise JobNotFound()
            if job.user_id != user_id:
                raise JobForbidden()
            repo = JobsRepository(session)
            events = await repo.list_stage_events(job_id)
            for e in events:
                session.expunge(e)
            return events

    async def finalize_from_webhook(self, event: FalWebhookEvent) -> None:
        async with self._sessionmaker() as session:
            jobs_repo = JobsRepository(session)
            job = await jobs_repo.get_by_provider_request_id(event.request_id)
        if job is None:
            logger.warning(
                "fal webhook for unknown request_id=%s — ignoring",
                event.request_id,
            )
            return
        if job.status in {
            JobStatus.succeeded,
            JobStatus.failed,
            JobStatus.canceled,
        }:
            logger.info(
                "fal webhook for already-finished job=%s (status=%s) — skipping",
                job.id,
                job.status.value,
            )
            return

        # Текущая async-стадия определяется по current_stage
        current = job.current_stage or JobStage.music_generation

        if event.status in {"failed", "canceled"}:
            await self._pipeline.fail(
                job_id=job.id,
                failed_stage=current,
                error_code=(
                    "PROVIDER_FAILED"
                    if event.status == "failed"
                    else "PROVIDER_CANCELED"
                ),
                error_message=event.error_message or event.status,
            )
            return

        if event.status != "completed":
            # in_progress — ничего не делаем
            return

        try:
            await self._pipeline.advance(
                job_id=job.id,
                completed_stage=current,
                audio_url=event.audio_url,
                duration_seconds=event.duration_seconds,
                stems=event.stems,
                event_id=event.event_id,
            )
        except (FalProviderError, FalTimeout) as exc:
            await self._pipeline.fail(
                job_id=job.id,
                failed_stage=current,
                error_code=exc.code,
                error_message=exc.message,
            )


def _collect_urls(payload: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    instr = payload.get("instruments") or {}
    harmonic = instr.get("harmonic") or {}
    for key in ("bass", "lead", "chord"):
        ref = harmonic.get(key) or {}
        url = ref.get("sample_url")
        if url:
            urls.append(url)
    drums = instr.get("drums") or {}
    for key in ("kick", "snare", "open_hihat", "closed_hihat"):
        ref = drums.get(key) or {}
        url = ref.get("sample_url")
        if url:
            urls.append(url)
    for aux in drums.get("auxiliary") or []:
        url = (aux or {}).get("sample_url")
        if url:
            urls.append(url)
    for key in ("mixing", "sound_effects"):
        ref = instr.get(key) or {}
        url = ref.get("sample_url")
        if url:
            urls.append(url)
    voice = payload.get("voice_url")
    if voice:
        urls.append(voice)
    return urls
