"""CatalogService — листинг битов и сэмплов с фильтрами."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.music.enums import BeatGenre, SampleCategory
from app.music.models import Beat, Sample
from app.music.repositories.beats import BeatsRepository
from app.music.repositories.samples import SamplesRepository


class CatalogService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list_beats(
        self,
        *,
        genre: BeatGenre | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Beat]:
        async with self._sessionmaker() as session:
            repo = BeatsRepository(session)
            return await repo.list_active(
                genre=genre, limit=limit, offset=offset
            )

    async def list_samples(
        self,
        *,
        category: SampleCategory | None = None,
        tag: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Sample]:
        async with self._sessionmaker() as session:
            repo = SamplesRepository(session)
            return await repo.list_active(
                category=category, tag=tag, limit=limit, offset=offset
            )
