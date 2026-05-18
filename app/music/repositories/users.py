from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.models import MusicUser, TokenWallet


class MusicUsersRepository:
    """Per-device user identity (X-User-Id → MusicUser)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, *, external_id: str) -> MusicUser:
        """Atomic upsert by `external_id` (race-safe under concurrent first touch)."""
        stmt = (
            pg_insert(MusicUser)
            .values(external_id=external_id)
            .on_conflict_do_update(
                index_elements=["external_id"],
                set_={"external_id": external_id},  # no-op update for RETURNING
            )
            .returning(MusicUser.id, MusicUser.external_id)
        )
        result = await self._session.execute(stmt)
        row = result.one()
        user = await self._session.get(MusicUser, row.id)
        assert user is not None  # just upserted
        # Ensure a wallet exists for this user.
        wallet_stmt = (
            pg_insert(TokenWallet)
            .values(user_id=user.id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        await self._session.execute(wallet_stmt)
        return user

    async def get_by_external_id(self, external_id: str) -> MusicUser | None:
        stmt = select(MusicUser).where(MusicUser.external_id == external_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
