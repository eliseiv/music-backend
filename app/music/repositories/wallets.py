from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.models import TokenWallet


class WalletsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, user_id: UUID) -> TokenWallet | None:
        """Locks the wallet row for the rest of the current transaction."""
        stmt = (
            select(TokenWallet)
            .where(TokenWallet.user_id == user_id)
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def ensure_exists(self, user_id: UUID) -> TokenWallet:
        await self._session.execute(
            pg_insert(TokenWallet)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        stmt = (
            select(TokenWallet)
            .where(TokenWallet.user_id == user_id)
            .with_for_update()
        )
        wallet = (await self._session.execute(stmt)).scalar_one()
        return wallet
