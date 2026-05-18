from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import SampleCategory
from app.music.models import Sample


class SamplesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(
        self,
        *,
        category: SampleCategory | None = None,
        tag: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Sample]:
        stmt = (
            select(Sample)
            .where(Sample.active.is_(True))
            .order_by(Sample.category, Sample.sort_order, Sample.title)
            .limit(limit)
            .offset(offset)
        )
        if category is not None:
            stmt = stmt.where(Sample.category == category)
        if tag is not None:
            stmt = stmt.where(Sample.tags.contains(array([tag])))
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)
