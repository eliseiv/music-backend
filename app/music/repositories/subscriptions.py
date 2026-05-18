from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import SubscriptionStatus
from app.music.models import SubscriptionState


class SubscriptionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, user_id: UUID) -> SubscriptionState | None:
        stmt = (
            select(SubscriptionState)
            .where(SubscriptionState.user_id == user_id)
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def ensure_exists(self, user_id: UUID) -> SubscriptionState:
        await self._session.execute(
            pg_insert(SubscriptionState)
            .values(user_id=user_id, status=SubscriptionStatus.none)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        stmt = (
            select(SubscriptionState)
            .where(SubscriptionState.user_id == user_id)
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one()
