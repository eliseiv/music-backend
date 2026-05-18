from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import JobStage, JobStatus
from app.music.models import Job, JobStageLog


class JobsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: UUID,
        provider_model: str,
        pricing_rule_id: UUID,
        reserved_tokens: int,
        input_payload: dict[str, Any],
        store_stems: bool,
        client_idempotency_key: str | None = None,
    ) -> Job:
        job = Job(
            user_id=user_id,
            status=JobStatus.queued,
            stage=None,
            provider_model=provider_model,
            pricing_rule_id=pricing_rule_id,
            reserved_tokens=reserved_tokens,
            captured_tokens=0,
            input_payload=input_payload,
            store_stems=store_stems,
            client_idempotency_key=client_idempotency_key,
        )
        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def get_by_id(self, job_id: UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def get_by_id_for_update(self, job_id: UUID) -> Job | None:
        """Lock job row for the duration of the current transaction."""
        stmt = select(Job).where(Job.id == job_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_provider_request_id(
        self, provider_request_id: str
    ) -> Job | None:
        stmt = select(Job).where(
            Job.provider_request_id == provider_request_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_provider_request_id_for_update(
        self, provider_request_id: str
    ) -> Job | None:
        stmt = (
            select(Job)
            .where(Job.provider_request_id == provider_request_id)
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(
        self, *, user_id: UUID, key: str
    ) -> Job | None:
        stmt = select(Job).where(
            Job.user_id == user_id, Job.client_idempotency_key == key
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_after_submit(
        self,
        *,
        job_id: UUID,
        provider_request_id: str,
        stage: JobStage,
    ) -> None:
        """Lock-then-mutate: захватываем строку до изменения."""
        job = await self.get_by_id_for_update(job_id)
        if job is None:
            return
        job.status = JobStatus.processing
        job.stage = stage
        job.current_stage = stage
        job.provider_request_id = provider_request_id
        if job.started_at is None:
            job.started_at = datetime.now(tz=timezone.utc)
        await self._session.flush()

    async def set_current_stage(
        self,
        *,
        job_id: UUID,
        stage: JobStage,
        provider_request_id: str | None,
    ) -> None:
        """Advance к следующей async-стадии: обновляет current_stage и
        provider_request_id (которым придёт webhook)."""
        job = await self.get_by_id_for_update(job_id)
        if job is None:
            return
        job.status = JobStatus.processing
        job.stage = stage
        job.current_stage = stage
        if provider_request_id is not None:
            job.provider_request_id = provider_request_id
        await self._session.flush()

    async def mark_failed(
        self,
        *,
        job_id: UUID,
        error_code: str,
        error_message: str,
    ) -> None:
        job = await self.get_by_id_for_update(job_id)
        if job is None:
            return
        job.status = JobStatus.failed
        job.error_code = error_code
        job.error_message = error_message
        job.finished_at = datetime.now(tz=timezone.utc)
        await self._session.flush()

    async def mark_succeeded(
        self,
        *,
        job_id: UUID,
        captured_tokens: int,
    ) -> None:
        job = await self.get_by_id_for_update(job_id)
        if job is None:
            return
        job.status = JobStatus.succeeded
        job.stage = JobStage.finalize
        job.current_stage = JobStage.finalize
        job.captured_tokens = captured_tokens
        job.finished_at = datetime.now(tz=timezone.utc)
        await self._session.flush()

    # --- stage log (job_stage_log) ---

    async def record_stage_event(
        self,
        *,
        job_id: UUID,
        stage: JobStage,
        status: str,
        error: str | None = None,
    ) -> None:
        """Upsert stage event. status: pending | running | succeeded | failed | skipped."""
        now = datetime.now(tz=timezone.utc)
        stmt = (
            select(JobStageLog)
            .where(JobStageLog.job_id == job_id, JobStageLog.stage == stage)
            .with_for_update()
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            entry = JobStageLog(
                job_id=job_id,
                stage=stage,
                status=status,
                started_at=now if status == "running" else None,
                finished_at=(
                    now
                    if status in {"succeeded", "failed", "skipped"}
                    else None
                ),
                error=error,
            )
            self._session.add(entry)
            await self._session.flush()
            return
        existing.status = status
        if status == "running" and existing.started_at is None:
            existing.started_at = now
        if status in {"succeeded", "failed", "skipped"}:
            existing.finished_at = now
        if error is not None:
            existing.error = error
        await self._session.flush()

    async def list_stage_events(self, job_id: UUID) -> list[JobStageLog]:
        stmt = (
            select(JobStageLog)
            .where(JobStageLog.job_id == job_id)
            .order_by(JobStageLog.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_orphans(self) -> list[Job]:
        """Jobs that were left in queued without provider_request_id (lost on restart)."""
        stmt = select(Job).where(
            Job.status == JobStatus.queued, Job.provider_request_id.is_(None)
        )
        return list((await self._session.execute(stmt)).scalars().all())
