from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import BeatGenre
from app.music.models import Beat


class BeatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(
        self,
        *,
        genre: BeatGenre | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Beat]:
        stmt = (
            select(Beat)
            .where(Beat.active.is_(True))
            .order_by(Beat.genre, Beat.sort_order, Beat.title)
            .limit(limit)
            .offset(offset)
        )
        if genre is not None:
            stmt = stmt.where(Beat.genre == genre)
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)
