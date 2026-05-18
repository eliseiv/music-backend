from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.models import Track


class TracksRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        job_id: UUID,
        user_id: UUID,
        audio_url: str,
        duration_seconds: float,
        stems: dict[str, Any] | None,
        meta: dict[str, Any] | None = None,
    ) -> Track:
        track = Track(
            job_id=job_id,
            user_id=user_id,
            audio_url=audio_url,
            duration_seconds=Decimal(str(duration_seconds)),
            stems=stems,
            meta=meta,
        )
        self._session.add(track)
        await self._session.flush()
        await self._session.refresh(track)
        return track

    async def get_by_id(self, track_id: UUID) -> Track | None:
        return await self._session.get(Track, track_id)

    async def get_by_job_id(self, job_id: UUID) -> Track | None:
        stmt = select(Track).where(Track.job_id == job_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
