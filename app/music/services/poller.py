"""Polling worker — фоновая задача, опрашивающая fal queue API.

Используется как **fallback** для webhook'ов от fal. fal подписывает свои
webhook'и Ed25519-ключом (а не нашим HMAC-секретом), поэтому без настройки
JWK-verification webhook'и нам не доходят. Polling — надёжный способ
получить итог.

Каждые `MUSIC_POLL_INTERVAL_SECONDS` секунд:
  1. Берёт все jobs со status IN ('queued','processing') у которых есть
     provider_request_id.
  2. Для каждого вызывает `fal.fetch_status(model, request_id)`.
  3. Если статус COMPLETED — вызывает `Pipeline.advance(...)`.
  4. Если FAILED/CANCELED — вызывает `Pipeline.fail(...)`.

Работает идемпотентно: повторный COMPLETED для уже succeeded job игнорируется
(внутри Pipeline.advance проверяется current_stage).
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import FalProviderError, FalTimeout
from app.config import Settings
from app.music.enums import JobStage, JobStatus
from app.music.models import Job
from app.music.providers.fal.base import FalProvider, FalStatusResult
from app.music.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


class FalPoller:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        fal: FalProvider,
        pipeline: Pipeline,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._fal = fal
        self._pipeline = pipeline
        self._settings = settings
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="fal-poller")
            logger.info(
                "FalPoller started (interval=%ss)",
                self._settings.MUSIC_POLL_INTERVAL_SECONDS,
            )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        interval = max(2.0, float(self._settings.MUSIC_POLL_INTERVAL_SECONDS))
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception:
                logger.exception("FalPoller iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self) -> None:
        jobs = await self._fetch_active_jobs()
        if not jobs:
            return
        logger.debug("FalPoller: %d active jobs", len(jobs))
        for j in jobs:
            try:
                result = await self._fal.fetch_status(
                    model=j["provider_model"], request_id=j["provider_request_id"]
                )
            except (FalProviderError, FalTimeout) as exc:
                logger.warning(
                    "FalPoller: fetch_status failed for job=%s rid=%s: %s",
                    j["id"],
                    j["provider_request_id"],
                    exc,
                )
                continue
            await self._apply(j, result)

    async def _fetch_active_jobs(self) -> list[dict]:
        async with self._sessionmaker() as session:
            stmt = (
                select(
                    Job.id,
                    Job.user_id,
                    Job.provider_model,
                    Job.provider_request_id,
                    Job.current_stage,
                )
                .where(
                    Job.status.in_([JobStatus.queued, JobStatus.processing]),
                    Job.provider_request_id.is_not(None),
                )
                .limit(50)
            )
            rows = (await session.execute(stmt)).all()
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "provider_model": r[2],
                    "provider_request_id": r[3],
                    "current_stage": r[4],
                }
                for r in rows
            ]

    async def _apply(self, j: dict, result: FalStatusResult) -> None:
        status = result.status.upper()
        if status in ("IN_QUEUE", "IN_PROGRESS"):
            return
        current_stage: JobStage | None = j["current_stage"] or JobStage.music_generation
        if status == "COMPLETED":
            await self._pipeline.advance(
                job_id=j["id"],
                completed_stage=current_stage,
                audio_url=result.audio_url,
                duration_seconds=result.duration_seconds,
                stems=result.stems,
                event_id=f"poll:{j['provider_request_id']}",
            )
            logger.info(
                "FalPoller: completed job=%s stage=%s",
                j["id"],
                current_stage.value,
            )
        elif status in ("FAILED", "CANCELED", "ERROR"):
            await self._pipeline.fail(
                job_id=j["id"],
                failed_stage=current_stage,
                error_code="PROVIDER_FAILED"
                if status != "CANCELED"
                else "PROVIDER_CANCELED",
                error_message=result.error_message or status,
            )
            logger.info(
                "FalPoller: failed job=%s stage=%s reason=%s",
                j["id"],
                current_stage.value,
                result.error_message,
            )
