"""GenerationService — оркестрация create_job/finalize.

create_job() (синхронная часть, до возврата HTTP-ответа):
  1. SubscriptionGate.ensure_active
  2. PricingService.resolve_active_rule + required_tokens_for_precharge
  3. Создать job-строку, WalletService.reserve(reserved_tokens)
  4. Сабмитить в fal (submit_music_generation)
  5. Сохранить provider_request_id и stage=music_generation
  6. Вернуть {jobId, status=queued/processing, tokensReserved}

finalize_from_webhook() (вызывается из webhook-обработчика):
  - succeeded → wallet.capture(actual_tokens, prev_reserved) + INSERT tracks
  - failed/canceled → wallet.release(reserved)
  - идемпотентно по job_id (двойной webhook не даст эффекта)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
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
from app.music.models import Beat, Job, Track
from app.music.providers.fal.base import FalProvider, FalWebhookEvent
from app.music.repositories.jobs import JobsRepository
from app.music.repositories.tracks import TracksRepository
from app.music.services.pricing_service import PricingService
from app.music.services.subscription_gate import SubscriptionGate
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

    async def create_job(
        self,
        *,
        user_id: UUID,
        request_payload: dict[str, Any],
        store_stems: bool,
        desired_duration_seconds: int | None,
    ) -> CreateJobResult:
        # 1. Подписка
        await self._gate.ensure_active(user_id)
        # 2. Проверим, что beat существует
        async with self._sessionmaker() as session:
            beat = await session.get(Beat, request_payload.get("beat_id"))
            if beat is None or not beat.active:
                raise BeatNotFound()

        # 3. Pricing
        rule = await self._pricing.resolve_active_rule(
            provider_model=self._settings.FAL_MUSIC_MODEL
        )
        reserved_tokens = self._pricing.required_tokens_for_precharge(
            rule,
            requested_duration_seconds=desired_duration_seconds,
        )

        # 4. Создаём job + резерв токенов в одной транзакции
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
                )
                job_id = job.id
        await self._wallet.reserve(
            user_id=user_id,
            amount=reserved_tokens,
            ref_type="job",
            ref_id=str(job_id),
        )

        # 5. Сабмит в fal (синхронно — fal сам queue'ит и пришлёт webhook)
        prompt = _compose_prompt(request_payload, beat)
        webhook_url = self._webhook_url()
        try:
            submit = await self._fal.submit_music_generation(
                prompt=prompt,
                duration_seconds=desired_duration_seconds,
                lyrics=request_payload.get("lyrics_prompt"),
                reference_audio_url=request_payload.get("voice_url"),
                webhook_url=webhook_url,
                idempotency_key=str(job_id),
            )
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

        # 6. Сохраняем provider_request_id, переводим в processing
        async with self._sessionmaker() as session:
            async with session.begin():
                jobs = JobsRepository(session)
                await jobs.update_after_submit(
                    job_id=job_id,
                    provider_request_id=submit.request_id,
                    stage=JobStage.music_generation,
                )
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
        if job.status in {JobStatus.succeeded, JobStatus.failed, JobStatus.canceled}:
            logger.info(
                "fal webhook for already-finished job=%s (status=%s) — skipping",
                job.id,
                job.status.value,
            )
            return

        if event.status in {"failed", "canceled"}:
            await self._wallet.release(
                user_id=job.user_id,
                amount=job.reserved_tokens,
                ref_type="job",
                ref_id=str(job.id),
            )
            async with self._sessionmaker() as session:
                async with session.begin():
                    repo = JobsRepository(session)
                    await repo.mark_failed(
                        job_id=job.id,
                        error_code=("fal_canceled" if event.status == "canceled" else "fal_failed"),
                        error_message=event.error_message or event.status,
                    )
            return

        if event.status != "completed":
            # in_progress — обновим stage и выходим
            return

        # completed
        rule = await self._pricing.resolve_active_rule(
            provider_model=job.provider_model
        )
        actual_duration = event.duration_seconds or 0.0
        captured_tokens = (
            self._pricing.required_tokens_for_capture(
                rule, actual_duration_seconds=actual_duration
            )
            if actual_duration > 0
            else job.reserved_tokens
        )
        captured_tokens = min(captured_tokens, job.reserved_tokens)

        await self._wallet.capture(
            user_id=job.user_id,
            amount=captured_tokens,
            previously_reserved=job.reserved_tokens,
            ref_type="job",
            ref_id=str(job.id),
        )
        if not event.audio_url:
            # Странно — completed без audio_url; помечаем failed.
            async with self._sessionmaker() as session:
                async with session.begin():
                    repo = JobsRepository(session)
                    await repo.mark_failed(
                        job_id=job.id,
                        error_code="fal_no_audio",
                        error_message="completed event missing audio_url",
                    )
            return
        async with self._sessionmaker() as session:
            async with session.begin():
                tracks = TracksRepository(session)
                existing = await tracks.get_by_job_id(job.id)
                if existing is None:
                    await tracks.add(
                        job_id=job.id,
                        user_id=job.user_id,
                        audio_url=event.audio_url,
                        duration_seconds=actual_duration,
                        stems=event.stems if job.store_stems else None,
                        meta={"event_id": event.event_id},
                    )
                repo = JobsRepository(session)
                await repo.mark_succeeded(
                    job_id=job.id,
                    captured_tokens=captured_tokens,
                )

    def _webhook_url(self) -> str | None:
        base = (self._settings.PUBLIC_BASE_URL or "").rstrip("/")
        if not base:
            return None
        return f"{base}/api/v1/music/webhooks/fal"


def _compose_prompt(request: dict[str, Any], beat: Beat) -> str:
    eq = request.get("equalizer") or {}
    instruments = request.get("instruments") or {}
    lyrics = request.get("lyrics_prompt")
    parts = [
        f"Genre: {beat.genre}",
        f"BPM: {eq.get('tempo') or beat.bpm}",
    ]
    if request.get("production"):
        parts.append(f"Production: {request['production']}")
    if request.get("pitch"):
        parts.append(f"Pitch: {request['pitch']}")
    if lyrics:
        parts.append(f"Lyrics theme: {lyrics}")
    densities = (
        f"densities: lead={eq.get('lead_density')}, bass={eq.get('bass_density')}, "
        f"chord={eq.get('chord_density')}, drum={eq.get('drum_density')}"
    )
    parts.append(densities)
    parts.append(f"reference_beat={beat.audio_url}")
    if instruments:
        parts.append(f"instruments_count={_count_samples(instruments)}")
    return " | ".join(parts)


def _count_samples(instr: dict[str, Any]) -> int:
    count = 0
    harmonic = instr.get("harmonic") or {}
    for key in ("bass", "lead", "chord"):
        if harmonic.get(key):
            count += 1
    drums = instr.get("drums") or {}
    for key in ("kick", "snare", "open_hihat", "closed_hihat"):
        if drums.get(key):
            count += 1
    count += len(drums.get("auxiliary") or [])
    if instr.get("mixing"):
        count += 1
    if instr.get("sound_effects"):
        count += 1
    return count
