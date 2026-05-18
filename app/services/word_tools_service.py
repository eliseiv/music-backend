from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.word_tools.base import (
    WordToolsItem,
    WordToolsProvider,
    WordToolsResult,
)
from app.repositories.search_requests import SearchRequestsRepository

logger = logging.getLogger(__name__)


@dataclass
class WordToolsServiceResult:
    query: str
    criterion: str
    total: int
    items: list[WordToolsItem]
    prompt_version: str | None


class WordToolsService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: WordToolsProvider,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._provider = provider

    async def search(
        self,
        *,
        user_id: UUID,
        query: str,
        criterion: str,
        limit: int,
        offset: int,
    ) -> WordToolsServiceResult:
        result_count = 0
        try:
            result: WordToolsResult = await self._provider.search(
                query=query,
                criterion=criterion,
                limit=limit,
                offset=offset,
            )
            result_count = len(result.items)
            return WordToolsServiceResult(
                query=query,
                criterion=criterion,
                total=result.total,
                items=result.items,
                prompt_version=result.prompt_version,
            )
        finally:
            try:
                async with self._sessionmaker() as session:
                    async with session.begin():
                        repo = SearchRequestsRepository(session)
                        await repo.add(
                            user_id=user_id,
                            query=query,
                            criterion=criterion,
                            result_count=result_count,
                        )
            except Exception:
                logger.exception("Failed to write search_requests analytics row")
