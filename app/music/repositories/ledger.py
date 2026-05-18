from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import TokenLedgerKind
from app.music.models import TokenLedgerEntry


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def make_idempotency_key(
        ref_type: str, ref_id: str, kind: TokenLedgerKind
    ) -> str:
        return f"{ref_type}:{ref_id}:{kind.value}"

    async def insert_or_get(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        kind: TokenLedgerKind,
        amount: int,
        balance_after_available: int,
        balance_after_reserved: int,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None = None,
    ) -> tuple[TokenLedgerEntry, bool]:
        """Insert a ledger entry; on conflict return the existing one.

        Returns (entry, inserted_now).
        """
        key = self.make_idempotency_key(ref_type, ref_id, kind)
        values = {
            "user_id": user_id,
            "wallet_id": wallet_id,
            "kind": kind,
            "amount": amount,
            "balance_after_available": balance_after_available,
            "balance_after_reserved": balance_after_reserved,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "idempotency_key": key,
            "meta": meta,
        }
        stmt = (
            pg_insert(TokenLedgerEntry)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(TokenLedgerEntry.id)
        )
        result = await self._session.execute(stmt)
        new_id = result.scalar_one_or_none()
        if new_id is not None:
            entry = await self._session.get(TokenLedgerEntry, new_id)
            assert entry is not None
            return entry, True

        # Conflict — fetch the existing entry by idempotency_key.
        existing = (
            await self._session.execute(
                select(TokenLedgerEntry).where(
                    TokenLedgerEntry.idempotency_key == key
                )
            )
        ).scalar_one()
        return existing, False
