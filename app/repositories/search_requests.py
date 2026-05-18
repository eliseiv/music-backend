from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search_request import SearchRequest


class SearchRequestsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: UUID,
        query: str,
        criterion: str,
        result_count: int,
    ) -> SearchRequest:
        row = SearchRequest(
            user_id=user_id,
            query=query,
            criterion=criterion,
            result_count=result_count,
        )
        self._session.add(row)
        await self._session.flush()
        return row
