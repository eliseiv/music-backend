from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import JobStage, JobStatus
from app.music.models import Job


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
        )
        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def get_by_id(self, job_id: UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def get_by_provider_request_id(
        self, provider_request_id: str
    ) -> Job | None:
        stmt = select(Job).where(
            Job.provider_request_id == provider_request_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_after_submit(
        self,
        *,
        job_id: UUID,
        provider_request_id: str,
        stage: JobStage,
    ) -> None:
        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.processing,
                stage=stage,
                provider_request_id=provider_request_id,
            )
        )

    async def mark_failed(
        self,
        *,
        job_id: UUID,
        error_code: str,
        error_message: str,
    ) -> None:
        from datetime import datetime, timezone

        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.failed,
                error_code=error_code,
                error_message=error_message,
                finished_at=datetime.now(tz=timezone.utc),
            )
        )

    async def mark_succeeded(
        self,
        *,
        job_id: UUID,
        captured_tokens: int,
    ) -> None:
        from datetime import datetime, timezone

        await self._session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.succeeded,
                stage=JobStage.finalize,
                captured_tokens=captured_tokens,
                finished_at=datetime.now(tz=timezone.utc),
            )
        )

    async def list_orphans(self) -> list[Job]:
        """Jobs that were left in queued without provider_request_id (lost on restart)."""
        stmt = select(Job).where(
            Job.status == JobStatus.queued, Job.provider_request_id.is_(None)
        )
        return list((await self._session.execute(stmt)).scalars().all())
