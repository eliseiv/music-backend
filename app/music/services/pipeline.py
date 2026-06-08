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

        # 2. lyrics — LLM-генерация текста из lyrics_prompt (если есть)
        generated_lyrics = await self._maybe_generate_lyrics(job, job_id)

        # 3. music_generation (async fal)
        await self._submit_music_generation(job, beat, lyrics=generated_lyrics)
        return JobStage.music_generation

    async def _maybe_generate_lyrics(
        self, job: Job, job_id: UUID
    ) -> str | None:
        """Если в payload есть lyrics_prompt — вызвать LLM. При ошибке
        логируем и продолжаем без lyrics (опциональная стадия)."""
        payload = job.input_payload or {}
        theme = payload.get("lyrics_prompt")
        if not theme:
            await self._record_stage(job_id, JobStage.lyrics, "skipped")
            return None
        await self._record_stage(job_id, JobStage.lyrics, "running")
        language = (payload.get("language") or "en")
        try:
            lyrics = await self._fal.generate_lyrics(
                prompt=theme, language=language
            )
        except (FalProviderError, FalTimeout) as exc:
            logger.warning(
                "lyrics generation failed for job=%s: %s — продолжаем без lyrics",
                job_id,
                exc,
            )
            await self._record_stage(
                job_id, JobStage.lyrics, "failed", error=str(exc)
            )
            return None
        if not lyrics or len(lyrics) < 3:
            await self._record_stage(
                job_id, JobStage.lyrics, "skipped", error="empty LLM output"
            )
            return None
        # Сохраним в payload для аудита
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                j = await repo.get_by_id_for_update(job_id)
                if j is not None:
                    p = dict(j.input_payload or {})
                    p["_generated_lyrics"] = lyrics
                    j.input_payload = p
        await self._record_stage(job_id, JobStage.lyrics, "succeeded")
        return lyrics

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

    FALLBACK_MUSIC_MODEL = "fal-ai/stable-audio"
    FALLBACK_VOCAL_MODEL = "fal-ai/ace-step"

    async def fail(
        self,
        *,
        job_id: UUID,
        failed_stage: JobStage,
        error_code: str,
        error_message: str,
    ) -> None:
        """Вызывается из fal webhook/poller на failed/canceled.

        Fallback: если music_generation упал с PROVIDER_FAILED от minimax-music —
        пробуем stable-audio (один раз). Job остаётся `processing`, poller подхватит
        новый provider_request_id.
        """
        if failed_stage == JobStage.music_generation and error_code == "PROVIDER_FAILED":
            retried = await self._try_music_fallback(job_id, error_message)
            if retried:
                return

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

    async def _try_music_fallback(
        self, job_id: UUID, prev_error: str
    ) -> bool:
        """Smart fallback при сбое minimax-music:
        - если есть lyrics → ace-step (vocal-model, поёт)
        - иначе → stable-audio (instrumental)

        Запускается один раз (флаг _music_fallback_used в payload).
        """
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return False
            payload = dict(job.input_payload or {})
            if payload.get("_music_fallback_used"):
                return False
            if job.provider_model in (
                self.FALLBACK_MUSIC_MODEL,
                self.FALLBACK_VOCAL_MODEL,
            ):
                return False  # уже сами на fallback модели — не зацикливаемся
            session.expunge(job)

        lyrics = payload.get("_generated_lyrics") or payload.get("lyrics_prompt")
        if lyrics:
            return await self._fallback_to_ace_step(
                job_id, payload, lyrics, prev_error
            )
        return await self._fallback_to_stable_audio(
            job_id, payload, prev_error
        )

    async def _fallback_to_ace_step(
        self, job_id: UUID, payload: dict[str, Any], lyrics: str, prev_error: str
    ) -> bool:
        tags = self._ace_step_tags(payload)
        logger.info(
            "music fallback → ace-step (vocal): tags=%s lyrics_len=%d",
            tags,
            len(lyrics),
        )
        try:
            submit = await self._fal.submit_ace_step_vocal(
                tags=tags,
                lyrics=lyrics,
                webhook_url=self._webhook_url(),
                idempotency_key=f"{job_id}:music-fb-vocal",
            )
        except (FalProviderError, FalTimeout) as exc:
            logger.warning(
                "ace-step submit failed for job=%s: %s — пробуем stable-audio",
                job_id,
                exc,
            )
            return await self._fallback_to_stable_audio(
                job_id, payload, f"{prev_error} → ace-step also failed: {exc}"
            )
        await self._mark_fallback(
            job_id, self.FALLBACK_VOCAL_MODEL, submit.request_id, prev_error
        )
        return True

    async def _fallback_to_stable_audio(
        self, job_id: UUID, payload: dict[str, Any], prev_error: str
    ) -> bool:
        seconds = int(payload.get("desired_duration_seconds") or 30)
        seconds = max(10, min(47, seconds))  # stable-audio: 1..47
        prompt = self._fallback_prompt(payload)
        logger.info(
            "music fallback → stable-audio (instrumental): seconds=%s prompt=%.60s",
            seconds,
            prompt,
        )
        try:
            submit = await self._fal.submit_stable_audio(
                prompt=prompt,
                seconds_total=seconds,
                webhook_url=self._webhook_url(),
                idempotency_key=f"{job_id}:music-fb",
            )
        except (FalProviderError, FalTimeout) as exc:
            logger.warning(
                "stable-audio submit failed for job=%s: %s", job_id, exc
            )
            return False
        await self._mark_fallback(
            job_id, self.FALLBACK_MUSIC_MODEL, submit.request_id, prev_error
        )
        return True

    async def _mark_fallback(
        self,
        job_id: UUID,
        new_model: str,
        new_request_id: str,
        prev_error: str,
    ) -> None:
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = JobsRepository(session)
                job = await repo.get_by_id_for_update(job_id)
                if job is None:
                    return
                payload = dict(job.input_payload or {})
                payload["_music_fallback_used"] = True
                payload["_music_fallback_model"] = new_model
                payload["_music_fallback_prev_error"] = prev_error[:200]
                job.input_payload = payload
                job.provider_model = new_model
                job.provider_request_id = new_request_id
        await self._record_stage(job_id, JobStage.music_generation, "running")
        logger.info(
            "music fallback: submitted %s for job=%s rid=%s",
            new_model,
            job_id,
            new_request_id,
        )

    @staticmethod
    def _ace_step_tags(payload: dict[str, Any]) -> str:
        """Соберём comma-separated style tags для ace-step из payload."""
        parts: list[str] = []
        # Можно протащить genre/sub-genre если бы был — но в payload их нет напрямую.
        # Берём production/pitch как хинт стиля.
        if payload.get("production"):
            parts.append(payload["production"])
        if payload.get("pitch"):
            parts.append(payload["pitch"])
        eq = payload.get("equalizer") or {}
        if eq.get("tempo"):
            parts.append(f"{eq['tempo']} bpm")
        # Универсальный хинт чтобы был вокал
        parts.append("vocal")
        return ", ".join(parts)

    @staticmethod
    def _fallback_prompt(payload: dict[str, Any]) -> str:
        """Соберём короткий prompt для stable-audio (без reference URL)."""
        eq = payload.get("equalizer") or {}
        parts: list[str] = []
        # genre/bpm если есть лог
        if payload.get("lyrics_prompt"):
            parts.append(f"theme: {payload['lyrics_prompt'][:60]}")
        bpm = eq.get("tempo")
        if bpm:
            parts.append(f"{bpm} BPM")
        if payload.get("production"):
            parts.append(payload["production"])
        if payload.get("pitch"):
            parts.append(payload["pitch"])
        return ", ".join(parts) or "instrumental music"

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

    async def _submit_music_generation(
        self, job: Job, beat: Beat | None, *, lyrics: str | None = None
    ) -> None:
        await self._record_stage(job.id, JobStage.music_generation, "running")
        prompt = _compose_prompt(job.input_payload, beat)
        webhook_url = self._webhook_url()
        # fal-ai/minimax-music принимает reference_audio_url как **стилевой
        # референс**, не как референс голоса. Используем ТОЛЬКО URL бита.
        # voice_url пойдёт в отдельную vocal_tts стадию через voice_clone.
        reference_audio_url = beat.audio_url if beat is not None else None
        # lyrics — это уже сгенерированный LLM текст (из стадии lyrics).
        # Если LLM пропустили — берём lyrics_prompt как есть (backward-compat,
        # пользователь мог прислать готовый текст).
        final_lyrics = lyrics or (job.input_payload or {}).get("lyrics_prompt")
        try:
            submit = await self._fal.submit_music_generation(
                prompt=prompt,
                duration_seconds=(job.input_payload or {}).get(
                    "desired_duration_seconds"
                ),
                lyrics=final_lyrics,
                reference_audio_url=reference_audio_url,
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
        # Свежий payload: объект job устарел, _runtime уже в БД (см. _submit_vocal_tts).
        async with self._sessionmaker() as session:
            fresh = await session.get(Job, job.id)
            runtime = (fresh.input_payload or {}).get("_runtime") or {} if fresh else {}
        await self._finalize(job.id, runtime)

    async def _submit_vocal_tts(self, job: Job) -> None:
        """Клонирует голос пользователя и озвучивает им сгенерированные lyrics.

        Flow:
        1. voice_clone(voice_url) → custom_voice_id (через minimax/voice-clone)
        2. submit_speech(text=_generated_lyrics, voice_id=custom_voice_id)
        3. webhook принесёт vocal track → пойдёт в mix_master (вместе с music)
        """
        # Перечитываем свежий payload из БД: advance() уже сохранил _runtime с
        # music_generation через _persist_runtime, но объект job в памяти устарел.
        # Без этого при voice_clone/speech fail _finalize получит runtime без
        # music и упадёт "no audio_url", потеряв уже готовую музыку.
        async with self._sessionmaker() as session:
            fresh = await session.get(Job, job.id)
            payload = dict(fresh.input_payload or {}) if fresh else dict(job.input_payload or {})
        voice_url = payload.get("voice_url")
        # ВАЖНО: берём _generated_lyrics (вывод LLM-стадии), не lyrics_prompt.
        # lyrics_prompt — это ТЕМА от пользователя, не текст для озвучки.
        lyrics = payload.get("_generated_lyrics") or payload.get("lyrics_prompt")
        if not voice_url or not lyrics:
            await self._record_stage(job.id, JobStage.vocal_tts, "skipped")
            runtime = payload.get("_runtime") or {}
            await self._finalize(job.id, runtime)
            return
        await self._record_stage(job.id, JobStage.vocal_tts, "running")
        # Step 1: клонируем голос (sync, обычно ≤30s)
        cloned_voice_id = payload.get("_cloned_voice_id")
        if not cloned_voice_id:
            try:
                cloned_voice_id = await self._fal.voice_clone(audio_url=voice_url)
            except (FalProviderError, FalTimeout) as exc:
                logger.warning(
                    "voice_clone failed for job=%s: %s — skipping vocal_tts",
                    job.id,
                    exc,
                )
                await self._record_stage(
                    job.id, JobStage.vocal_tts, "failed", error=f"voice_clone: {exc}"
                )
                runtime = payload.get("_runtime") or {}
                await self._finalize(job.id, runtime)
                return
            # Сохраним voice_id чтобы при retry не клонировать повторно
            async with self._sessionmaker() as session:
                async with session.begin():
                    repo = JobsRepository(session)
                    j = await repo.get_by_id_for_update(job.id)
                    if j is not None:
                        p = dict(j.input_payload or {})
                        p["_cloned_voice_id"] = cloned_voice_id
                        j.input_payload = p
        # Step 2: TTS speech с клонированным голосом (через queue + webhook)
        try:
            submit = await self._fal.submit_speech(
                text=lyrics,
                voice_id=cloned_voice_id,
                webhook_url=self._webhook_url(),
                idempotency_key=f"{job.id}:tts",
            )
        except (FalProviderError, FalTimeout) as exc:
            await self._record_stage(
                job.id, JobStage.vocal_tts, "failed", error=str(exc)
            )
            # Не fail весь job — продолжаем с music без voice
            runtime = payload.get("_runtime") or {}
            await self._finalize(job.id, runtime)
            return
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

        # 6. mix_master — если есть и music и vocal_tts, делаем реальный микс
        # через ffmpeg. Иначе bookkeeping skip.
        runtime = await self._maybe_mix_master(job_id, runtime)

        # 7. upload_cdn (inline)
        await self._record_stage(job_id, JobStage.upload_cdn, "succeeded")

        # Выбираем итоговый audio_url: mix_master > refine > music_generation
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

        # Если fal не вернул duration — пробуем прочитать из самого файла
        if not final_duration or final_duration <= 0:
            from app.music.services.audio_duration import probe_duration_seconds

            probed = await probe_duration_seconds(final_audio_url)
            if probed and probed > 0:
                final_duration = probed
                logger.info(
                    "probed duration for job=%s: %.2fs", job_id, probed
                )

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

    async def _maybe_mix_master(
        self, job_id: UUID, runtime: dict[str, Any]
    ) -> dict[str, Any]:
        """Если есть и music_generation и vocal_tts output — микшируем через
        ffmpeg. Результат сохраняем в runtime['mix_master'] и возвращаем
        обновлённый runtime. В случае ошибки помечаем mix_master skipped
        и оставляем runtime как был.
        """
        from app.music.services.audio_mixer import (
            ffmpeg_available,
            mix_music_and_vocal,
        )

        music_url = (
            runtime.get(JobStage.audio_to_audio_refine.value, {}).get("audio_url")
            or runtime.get(JobStage.music_generation.value, {}).get("audio_url")
        )
        vocal_url = runtime.get(JobStage.vocal_tts.value, {}).get("audio_url")
        if not music_url or not vocal_url:
            await self._record_stage(job_id, JobStage.mix_master, "skipped")
            return runtime
        if not ffmpeg_available():
            logger.warning(
                "ffmpeg not available — mix_master skipped, vocal saved to stems"
            )
            await self._record_stage(
                job_id, JobStage.mix_master, "skipped", error="ffmpeg not in PATH"
            )
            # Сохраняем vocal в stems чтобы клиент мог его микшировать сам
            mg_key = JobStage.audio_to_audio_refine.value if JobStage.audio_to_audio_refine.value in runtime else JobStage.music_generation.value
            stems = dict(runtime[mg_key].get("stems") or {})
            stems["vocal"] = vocal_url
            runtime[mg_key]["stems"] = stems
            return runtime
        await self._record_stage(job_id, JobStage.mix_master, "running")
        try:
            mix_url, mix_duration = await mix_music_and_vocal(
                music_url=music_url,
                vocal_url=vocal_url,
                upload_fn=self._fal.upload_to_storage,
            )
        except Exception as e:
            logger.warning("mix_master failed for job=%s: %s", job_id, e)
            await self._record_stage(
                job_id, JobStage.mix_master, "failed", error=str(e)[:200]
            )
            return runtime
        if not mix_url:
            await self._record_stage(
                job_id, JobStage.mix_master, "failed", error="mix returned None"
            )
            return runtime
        runtime["mix_master"] = {
            "audio_url": mix_url,
            "duration_seconds": mix_duration,
            "stems": {"vocal": vocal_url, "music": music_url},
        }
        await self._record_stage(job_id, JobStage.mix_master, "succeeded")
        logger.info(
            "mix_master succeeded for job=%s: %s (%.2fs)",
            job_id, mix_url, mix_duration or 0,
        )
        return runtime

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
        has_voice = bool(payload.get("voice_url"))
        has_lyrics = bool(payload.get("lyrics_prompt"))
        # refine — opt-in: требует доп. полей (tags/original_tags) и оставлен
        # как фича для будущего. По умолчанию пропускается.
        enable_refine = bool(payload.get("enable_refine"))
        # vocal_tts — opt-in: требует voice_url + lyrics_prompt, иначе skip.
        if completed == JobStage.music_generation:
            if enable_refine and payload.get("beat_id"):
                return JobStage.audio_to_audio_refine
            if has_voice and has_lyrics:
                return JobStage.vocal_tts
            return None
        if completed == JobStage.audio_to_audio_refine:
            if has_voice and has_lyrics:
                return JobStage.vocal_tts
            return None
        # vocal_tts → finalize
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
    """Выбирает audio_url/duration/stems финального трека.

    Приоритет:
    1. mix_master (если ffmpeg успешно смикшировал music + vocal)
    2. audio_to_audio_refine (стилевая обработка)
    3. music_generation (база)

    vocal_tts НЕ выбирается напрямую — это просто vocal-layer, должен
    быть смикширован с music через ffmpeg в mix_master. Если ffmpeg
    недоступен или микс упал — финальным останется music (без vocal),
    а vocal сохраняется в stems['vocal'].
    """
    for key in (
        "mix_master",
        JobStage.audio_to_audio_refine.value,
        JobStage.music_generation.value,
    ):
        if key in runtime and runtime[key].get("audio_url"):
            r = runtime[key]
            return r.get("audio_url"), r.get("duration_seconds"), r.get("stems")
    return None, None, None
