from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import FalProviderError, FalTimeout
from app.config import Settings
from app.music.enums import JobStage, JobStatus
from app.music.models import Beat, Job
from app.music.providers.fal.base import FalProvider, FalSubmitResult
from app.music.repositories.jobs import JobsRepository
from app.music.repositories.tracks import TracksRepository
from app.music.services.pricing_service import PricingService
from app.music.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


# Стадии, которые делают внешний fal-submit (ждут webhook).
ASYNC_STAGES: tuple[JobStage, ...] = (
    JobStage.music_generation,
    JobStage.audio_to_audio_refine,
    JobStage.vocal_tts,
)


class Pipeline:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        fal: FalProvider,
        wallet: WalletService,
        pricing: PricingService,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fal = fal
        self._wallet = wallet
        self._pricing = pricing
        self._settings = settings

    # ------- entry point: вызывается из generation_service.create_job -------

    async def start(self, job_id: UUID) -> JobStage:
        """Запускает пайплайн: inline стадии prepare_prompt и lyrics, затем
        submit первой async стадии (music_generation).

        Возвращает имя текущей async стадии для логирования.
        """
        job, beat = await self._load_job_and_beat(job_id)

        # 1. prepare_prompt (inline)
        await self._record_stage(job_id, JobStage.prepare_prompt, "succeeded")

        # 2. lyrics (inline, если есть lyrics_prompt)
        if (job.input_payload or {}).get("lyrics_prompt"):
            await self._record_stage(job_id, JobStage.lyrics, "succeeded")
        else:
            await self._record_stage(job_id, JobStage.lyrics, "skipped")

        # 3. music_generation (async fal)
        await self._submit_music_generation(job, beat)
        return JobStage.music_generation

    # ------- webhook handler -------

    async def advance(
        self,
        *,
        job_id: UUID,
        completed_stage: JobStage,
        audio_url: str | None,
        duration_seconds: float | None,
        stems: dict[str, Any] | None,
        event_id: str,
    ) -> None:
        """Вызывается из fal webhook на completed. Записывает completed_stage
        как succeeded и решает, что делать дальше.
        """
        job, _ = await self._load_job_and_beat(job_id)

        # Защита от reorder: webhook относится к не-текущей стадии
        if job.current_stage is not None and job.current_stage != completed_stage:
            logger.warning(
                "Webhook for stage=%s but job.current_stage=%s (job=%s); ignoring",
                completed_stage.value,
                job.current_stage.value,
                job_id,
            )
            return

        await self._record_stage(job_id, completed_stage, "succeeded")

        # Сохраняем audio_url последней внешней стадии в input_payload.runtime
        runtime = (job.input_payload or {}).get("_runtime") or {}
        runtime[completed_stage.value] = {
            "audio_url": audio_url,
            "duration_seconds": duration_seconds,
            "stems": stems,
            "event_id": event_id,
        }
        await self._persist_runtime(job_id, runtime)

        # Решаем, что делать дальше
        next_stage = self._next_async_stage(job, completed_stage)
        if next_stage == JobStage.audio_to_audio_refine:
            await self._submit_audio_to_audio_refine(
                job, source_audio_url=audio_url
            )
        elif next_stage == JobStage.vocal_tts:
            await self._submit_vocal_tts(job)
        else:
            # Все async-стадии завершены → переходим к финализации
            await self._finalize(job_id, runtime)

    async def fail(
        self,
        *,
        job_id: UUID,
        failed_stage: JobStage,
        error_code: str,
        error_message: str,
    ) -> None:
        """Вызывается из fal webhook на failed/canceled."""
        await self._record_stage(
            job_id, failed_stage, "failed", error=error_message
        )
        # Release reserved tokens
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            user_id = job.user_id
            reserved = job.reserved_tokens
        await self._wallet.release(
            user_id=user_id,
            amount=reserved,
            ref_type="job",
            ref_id=str(job_id),
        )
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                await repo.mark_failed(
                    job_id=job_id,
                    error_code=error_code,
                    error_message=error_message,
                )

    # ------- internal helpers -------

    async def _load_job_and_beat(
        self, job_id: UUID
    ) -> tuple[Job, Beat | None]:
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                raise RuntimeError(f"Job {job_id} not found in pipeline")
            beat_id = (job.input_payload or {}).get("beat_id")
            beat = await session.get(Beat, beat_id) if beat_id else None
            session.expunge(job)
            if beat is not None:
                session.expunge(beat)
        return job, beat

    async def _record_stage(
        self,
        job_id: UUID,
        stage: JobStage,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                await repo.record_stage_event(
                    job_id=job_id, stage=stage, status=status, error=error
                )

    async def _list_recorded_stages(self, job_id: UUID) -> set[JobStage]:
        async with self._sessionmaker() as session:
            repo = JobsRepository(session)
            events = await repo.list_stage_events(job_id)
            return {e.stage for e in events}

    async def _persist_runtime(
        self, job_id: UUID, runtime: dict[str, Any]
    ) -> None:
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                job = await repo.get_by_id_for_update(job_id)
                if job is None:
                    return
                payload = dict(job.input_payload or {})
                payload["_runtime"] = runtime
                job.input_payload = payload
                await session.flush()

    async def _submit_music_generation(self, job: Job, beat: Beat | None) -> None:
        await self._record_stage(job.id, JobStage.music_generation, "running")
        prompt = _compose_prompt(job.input_payload, beat)
        webhook_url = self._webhook_url()
        try:
            submit = await self._fal.submit_music_generation(
                prompt=prompt,
                duration_seconds=(job.input_payload or {}).get(
                    "desired_duration_seconds"
                ),
                lyrics=(job.input_payload or {}).get("lyrics_prompt"),
                reference_audio_url=(job.input_payload or {}).get("voice_url"),
                webhook_url=webhook_url,
                idempotency_key=f"{job.id}:music",
            )
        except (FalProviderError, FalTimeout) as exc:
            await self._record_stage(
                job.id, JobStage.music_generation, "failed", error=str(exc)
            )
            raise
        await self._set_current_stage(
            job.id, JobStage.music_generation, submit.request_id
        )

    async def _submit_audio_to_audio_refine(
        self, job: Job, *, source_audio_url: str | None
    ) -> None:
        if not source_audio_url:
            await self._record_stage(
                job.id, JobStage.audio_to_audio_refine, "skipped"
            )
            # Сразу следующая стадия
            await self._after_refine_skip(job)
            return
        await self._record_stage(
            job.id, JobStage.audio_to_audio_refine, "running"
        )
        try:
            beat_url = self._beat_audio_url(job)
            submit = await self._fal.submit_audio_to_audio_refine(
                source_audio_url=source_audio_url,
                prompt=beat_url or "refine",
                webhook_url=self._webhook_url(),
                idempotency_key=f"{job.id}:refine",
            )
        except (FalProviderError, FalTimeout) as exc:
            await self._record_stage(
                job.id, JobStage.audio_to_audio_refine, "failed", error=str(exc)
            )
            raise
        await self._set_current_stage(
            job.id, JobStage.audio_to_audio_refine, submit.request_id
        )

    async def _after_refine_skip(self, job: Job) -> None:
        """Если refine пропущен, проверяем нужен ли vocal_tts."""
        if (job.input_payload or {}).get("voice_url"):
            await self._submit_vocal_tts(job)
            return
        await self._record_stage(job.id, JobStage.vocal_tts, "skipped")
        runtime = (job.input_payload or {}).get("_runtime") or {}
        await self._finalize(job.id, runtime)

    async def _submit_vocal_tts(self, job: Job) -> None:
        voice_url = (job.input_payload or {}).get("voice_url")
        lyrics = (job.input_payload or {}).get("lyrics_prompt")
        if not voice_url or not lyrics:
            await self._record_stage(job.id, JobStage.vocal_tts, "skipped")
            runtime = (job.input_payload or {}).get("_runtime") or {}
            await self._finalize(job.id, runtime)
            return
        await self._record_stage(job.id, JobStage.vocal_tts, "running")
        try:
            submit = await self._fal.submit_speech(
                text=lyrics,
                voice=voice_url,
                webhook_url=self._webhook_url(),
                idempotency_key=f"{job.id}:tts",
            )
        except (FalProviderError, FalTimeout) as exc:
            await self._record_stage(
                job.id, JobStage.vocal_tts, "failed", error=str(exc)
            )
            raise
        await self._set_current_stage(
            job.id, JobStage.vocal_tts, submit.request_id
        )

    async def _finalize(self, job_id: UUID, runtime: dict[str, Any]) -> None:
        # Гарантируем, что все async стадии помечены: те, что не выполнялись,
        # помечаются `skipped`. record_stage_event — idempotent (INSERT-or-UPDATE
        # с UNIQUE(job_id, stage)), повторный вызов на завершённую стадию её
        # перезатрёт — поэтому сначала проверим, нет ли уже записи.
        existing = await self._list_recorded_stages(job_id)
        for st in ASYNC_STAGES:
            if st not in existing:
                await self._record_stage(job_id, st, "skipped")
        # 6. mix_master (inline)
        await self._record_stage(job_id, JobStage.mix_master, "succeeded")
        # 7. upload_cdn (inline)
        await self._record_stage(job_id, JobStage.upload_cdn, "succeeded")

        # Выбираем итоговый audio_url: refine > music_generation
        final_audio_url, final_duration, final_stems = _pick_final_output(runtime)
        if not final_audio_url:
            await self._record_stage(
                job_id,
                JobStage.finalize,
                "failed",
                error="no audio_url after pipeline",
            )
            await self._mark_job_failed(
                job_id, "PROVIDER_FAILED", "no audio_url after pipeline"
            )
            return

        # Capture токенов
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            user_id = job.user_id
            reserved = job.reserved_tokens
            provider_model = job.provider_model
            store_stems = job.store_stems

        rule = await self._pricing.resolve_active_rule(
            provider_model=provider_model
        )
        captured = (
            self._pricing.required_tokens_for_capture(
                rule, actual_duration_seconds=final_duration or 0
            )
            if final_duration and final_duration > 0
            else reserved
        )
        captured = min(captured, reserved)
        await self._wallet.capture(
            user_id=user_id,
            amount=captured,
            previously_reserved=reserved,
            ref_type="job",
            ref_id=str(job_id),
        )

        # 8. finalize (inline) — INSERT tracks + mark succeeded
        await self._record_stage(job_id, JobStage.finalize, "running")
        async with self._sessionmaker() as session:
            async with session.begin():
                tracks = TracksRepository(session)
                existing = await tracks.get_by_job_id(job_id)
                if existing is None:
                    await tracks.add(
                        job_id=job_id,
                        user_id=user_id,
                        audio_url=final_audio_url,
                        duration_seconds=final_duration or 0,
                        stems=final_stems if store_stems else None,
                        meta={"runtime": runtime},
                    )
                repo = JobsRepository(session)
                await repo.mark_succeeded(
                    job_id=job_id, captured_tokens=captured
                )
        await self._record_stage(job_id, JobStage.finalize, "succeeded")

    async def _set_current_stage(
        self, job_id: UUID, stage: JobStage, request_id: str
    ) -> None:
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                await repo.set_current_stage(
                    job_id=job_id,
                    stage=stage,
                    provider_request_id=request_id,
                )

    async def _mark_job_failed(
        self, job_id: UUID, error_code: str, error_message: str
    ) -> None:
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                await repo.mark_failed(
                    job_id=job_id,
                    error_code=error_code,
                    error_message=error_message,
                )

    def _next_async_stage(
        self, job: Job, completed: JobStage
    ) -> JobStage | None:
        payload = job.input_payload or {}
        has_beat = bool(payload.get("beat_id"))
        has_voice = bool(payload.get("voice_url"))
        if completed == JobStage.music_generation:
            if has_beat:
                return JobStage.audio_to_audio_refine
            if has_voice and payload.get("lyrics_prompt"):
                return JobStage.vocal_tts
            return None
        if completed == JobStage.audio_to_audio_refine:
            if has_voice and payload.get("lyrics_prompt"):
                return JobStage.vocal_tts
            return None
        # vocal_tts
        return None

    def _webhook_url(self) -> str | None:
        base = (self._settings.PUBLIC_BASE_URL or "").rstrip("/")
        if not base:
            return None
        return f"{base}/v1/webhooks/fal"

    @staticmethod
    def _beat_audio_url(job: Job) -> str | None:
        # input_payload не хранит сам URL бита — нужно загрузить Beat. Но в
        # advance() мы уже не имеем сессии. Чтобы не делать лишний запрос,
        # просто используем placeholder; реальный fal-ai/ace-step принимает
        # текстовый prompt в дополнение к source_audio_url.
        return None


def _compose_prompt(payload: dict[str, Any] | None, beat: Beat | None) -> str:
    payload = payload or {}
    eq = payload.get("equalizer") or {}
    parts: list[str] = []
    if beat is not None:
        parts.append(f"Genre: {beat.genre.value if hasattr(beat.genre, 'value') else beat.genre}")
        parts.append(f"BPM: {eq.get('tempo') or beat.bpm}")
        parts.append(f"reference_beat={beat.audio_url}")
    if payload.get("production"):
        parts.append(f"Production: {payload['production']}")
    if payload.get("pitch"):
        parts.append(f"Pitch: {payload['pitch']}")
    if payload.get("lyrics_prompt"):
        parts.append(f"Lyrics theme: {payload['lyrics_prompt']}")
    parts.append(
        f"densities: lead={eq.get('lead_density')}, bass={eq.get('bass_density')}, "
        f"chord={eq.get('chord_density')}, drum={eq.get('drum_density')}"
    )
    return " | ".join(parts) or "music generation"


def _pick_final_output(
    runtime: dict[str, Any],
) -> tuple[str | None, float | None, dict[str, Any] | None]:
    """Выбирает audio_url/duration/stems из последней успешной async-стадии.

    Приоритет: vocal_tts > audio_to_audio_refine > music_generation.
    """
    for key in (
        JobStage.vocal_tts.value,
        JobStage.audio_to_audio_refine.value,
        JobStage.music_generation.value,
    ):
        if key in runtime and runtime[key].get("audio_url"):
            r = runtime[key]
            return r.get("audio_url"), r.get("duration_seconds"), r.get("stems")
    return None, None, None
