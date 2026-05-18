"""WalletService — атомарные операции с токен-кошельком.

Каждый метод:
- Открывает транзакцию (если её ещё нет в session) → `SELECT ... FOR UPDATE`.
- Записывает `token_ledger`-запись с `idempotency_key`, что делает повторный
  вызов с теми же `ref_type/ref_id/kind` безопасным.

Все методы возвращают `WalletOpResult(entry, inserted)` — caller может узнать,
была ли это новая операция или дубликат.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import InsufficientTokens, SubscriptionExpired
from app.music.enums import TokenLedgerKind
from app.music.models import TokenLedgerEntry, TokenWallet
from app.music.repositories.ledger import LedgerRepository
from app.music.repositories.wallets import WalletsRepository

logger = logging.getLogger(__name__)


@dataclass
class WalletBalance:
    available: int
    reserved: int
    frozen: bool


@dataclass
class WalletOpResult:
    entry: TokenLedgerEntry
    inserted: bool
    available: int
    reserved: int


class WalletService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get_balance(self, user_id: UUID) -> WalletBalance:
        async with self._sessionmaker() as session:
            wallets = WalletsRepository(session)
            wallet = await wallets.ensure_exists(user_id)
            return WalletBalance(
                available=wallet.available_tokens,
                reserved=wallet.reserved_tokens,
                frozen=wallet.frozen,
            )

    async def reserve(
        self,
        *,
        user_id: UUID,
        amount: int,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None = None,
    ) -> WalletOpResult:
        if amount <= 0:
            raise ValueError("amount must be positive")
        async with self._sessionmaker() as session:
            async with session.begin():
                wallets = WalletsRepository(session)
                ledger = LedgerRepository(session)
                wallet = await wallets.ensure_exists(user_id)
                key = ledger.make_idempotency_key(
                    ref_type, ref_id, TokenLedgerKind.debit_reserve
                )
                existing = await self._find_existing_entry(session, key)
                if existing is not None:
                    return WalletOpResult(
                        entry=existing,
                        inserted=False,
                        available=wallet.available_tokens,
                        reserved=wallet.reserved_tokens,
                    )
                if wallet.frozen:
                    raise SubscriptionExpired(
                        details={"reason": "wallet_frozen"}
                    )
                if wallet.available_tokens < amount:
                    raise InsufficientTokens(
                        details={
                            "required": amount,
                            "available": wallet.available_tokens,
                        }
                    )
                wallet.available_tokens -= amount
                wallet.reserved_tokens += amount
                entry, inserted = await ledger.insert_or_get(
                    user_id=user_id,
                    wallet_id=wallet.id,
                    kind=TokenLedgerKind.debit_reserve,
                    amount=-amount,
                    balance_after_available=wallet.available_tokens,
                    balance_after_reserved=wallet.reserved_tokens,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    meta=meta,
                )
                return WalletOpResult(
                    entry=entry,
                    inserted=inserted,
                    available=wallet.available_tokens,
                    reserved=wallet.reserved_tokens,
                )

    async def capture(
        self,
        *,
        user_id: UUID,
        amount: int,
        previously_reserved: int,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None = None,
    ) -> WalletOpResult:
        """Captures actual usage from a prior `reserve(previously_reserved)`.

        Works even if wallet is frozen — the tokens are already off the
        `available` balance.
        """
        if amount < 0 or previously_reserved < 0:
            raise ValueError("amount/previously_reserved must be non-negative")
        if amount > previously_reserved:
            # caller bug: capture larger than reserve → treat as insufficient
            raise InsufficientTokens(
                details={
                    "required": amount,
                    "available_for_capture": previously_reserved,
                }
            )
        delta_release = previously_reserved - amount
        async with self._sessionmaker() as session:
            async with session.begin():
                wallets = WalletsRepository(session)
                ledger = LedgerRepository(session)
                wallet = await wallets.ensure_exists(user_id)
                key = ledger.make_idempotency_key(
                    ref_type, ref_id, TokenLedgerKind.debit_capture
                )
                existing = await self._find_existing_entry(session, key)
                if existing is not None:
                    return WalletOpResult(
                        entry=existing,
                        inserted=False,
                        available=wallet.available_tokens,
                        reserved=wallet.reserved_tokens,
                    )
                wallet.reserved_tokens -= previously_reserved
                wallet.available_tokens += delta_release
                entry, inserted = await ledger.insert_or_get(
                    user_id=user_id,
                    wallet_id=wallet.id,
                    kind=TokenLedgerKind.debit_capture,
                    amount=-amount,
                    balance_after_available=wallet.available_tokens,
                    balance_after_reserved=wallet.reserved_tokens,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    meta={"previously_reserved": previously_reserved, **(meta or {})},
                )
                return WalletOpResult(
                    entry=entry,
                    inserted=inserted,
                    available=wallet.available_tokens,
                    reserved=wallet.reserved_tokens,
                )

    async def release(
        self,
        *,
        user_id: UUID,
        amount: int,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None = None,
    ) -> WalletOpResult:
        """Releases reserved tokens back to `available`. Used on failed/canceled."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        async with self._sessionmaker() as session:
            async with session.begin():
                wallets = WalletsRepository(session)
                ledger = LedgerRepository(session)
                wallet = await wallets.ensure_exists(user_id)
                key = ledger.make_idempotency_key(
                    ref_type, ref_id, TokenLedgerKind.credit_release
                )
                existing = await self._find_existing_entry(session, key)
                if existing is not None:
                    return WalletOpResult(
                        entry=existing,
                        inserted=False,
                        available=wallet.available_tokens,
                        reserved=wallet.reserved_tokens,
                    )
                clamped = min(amount, wallet.reserved_tokens)
                wallet.reserved_tokens -= clamped
                wallet.available_tokens += clamped
                entry, inserted = await ledger.insert_or_get(
                    user_id=user_id,
                    wallet_id=wallet.id,
                    kind=TokenLedgerKind.credit_release,
                    amount=clamped,
                    balance_after_available=wallet.available_tokens,
                    balance_after_reserved=wallet.reserved_tokens,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    meta=meta,
                )
                return WalletOpResult(
                    entry=entry,
                    inserted=inserted,
                    available=wallet.available_tokens,
                    reserved=wallet.reserved_tokens,
                )

    async def credit(
        self,
        *,
        user_id: UUID,
        amount: int,
        kind: TokenLedgerKind,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None = None,
    ) -> WalletOpResult:
        """Generic credit (purchase, subscription grant, refund).

        For REFUND with negative `amount`: clamps at 0 and emits a paired
        `debit_adjustment` ledger entry recording the unrecoverable diff.
        """
        if amount == 0:
            raise ValueError("amount must be non-zero")
        async with self._sessionmaker() as session:
            async with session.begin():
                wallets = WalletsRepository(session)
                ledger = LedgerRepository(session)
                wallet = await wallets.ensure_exists(user_id)
                key = ledger.make_idempotency_key(ref_type, ref_id, kind)
                existing = await self._find_existing_entry(session, key)
                if existing is not None:
                    return WalletOpResult(
                        entry=existing,
                        inserted=False,
                        available=wallet.available_tokens,
                        reserved=wallet.reserved_tokens,
                    )
                applied_amount = amount
                clamped_shortfall = 0
                if amount < 0:
                    abs_amount = -amount
                    if abs_amount > wallet.available_tokens:
                        clamped_shortfall = abs_amount - wallet.available_tokens
                        applied_amount = -wallet.available_tokens
                wallet.available_tokens += applied_amount
                entry, inserted = await ledger.insert_or_get(
                    user_id=user_id,
                    wallet_id=wallet.id,
                    kind=kind,
                    amount=applied_amount,
                    balance_after_available=wallet.available_tokens,
                    balance_after_reserved=wallet.reserved_tokens,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    meta=meta,
                )
                if clamped_shortfall > 0:
                    await ledger.insert_or_get(
                        user_id=user_id,
                        wallet_id=wallet.id,
                        kind=TokenLedgerKind.debit_adjustment,
                        amount=-clamped_shortfall,
                        balance_after_available=wallet.available_tokens,
                        balance_after_reserved=wallet.reserved_tokens,
                        ref_type=ref_type,
                        ref_id=ref_id + ":clamp",
                        meta={"reason": "refund_clamped_to_zero"},
                    )
                return WalletOpResult(
                    entry=entry,
                    inserted=inserted,
                    available=wallet.available_tokens,
                    reserved=wallet.reserved_tokens,
                )

    async def set_frozen(self, *, user_id: UUID, frozen: bool) -> WalletBalance:
        async with self._sessionmaker() as session:
            async with session.begin():
                wallets = WalletsRepository(session)
                wallet = await wallets.ensure_exists(user_id)
                wallet.frozen = frozen
            return WalletBalance(
                available=wallet.available_tokens,
                reserved=wallet.reserved_tokens,
                frozen=wallet.frozen,
            )

    # --- session-aware варианты для использования внутри общей транзакции
    # ВНУТРИ открытого `async with session.begin()`.

    @staticmethod
    async def set_frozen_in_session(
        session: AsyncSession, *, user_id: UUID, frozen: bool
    ) -> None:
        wallets = WalletsRepository(session)
        wallet = await wallets.ensure_exists(user_id)
        wallet.frozen = frozen
        await session.flush()

    @staticmethod
    async def credit_in_session(
        session: AsyncSession,
        *,
        user_id: UUID,
        amount: int,
        kind: TokenLedgerKind,
        ref_type: str,
        ref_id: str,
        meta: dict[str, Any] | None = None,
    ) -> WalletOpResult:
        if amount == 0:
            raise ValueError("amount must be non-zero")
        wallets = WalletsRepository(session)
        ledger = LedgerRepository(session)
        wallet = await wallets.ensure_exists(user_id)
        key = ledger.make_idempotency_key(ref_type, ref_id, kind)
        existing = await WalletService._find_existing_entry(session, key)
        if existing is not None:
            return WalletOpResult(
                entry=existing,
                inserted=False,
                available=wallet.available_tokens,
                reserved=wallet.reserved_tokens,
            )
        applied_amount = amount
        clamped_shortfall = 0
        if amount < 0:
            abs_amount = -amount
            if abs_amount > wallet.available_tokens:
                clamped_shortfall = abs_amount - wallet.available_tokens
                applied_amount = -wallet.available_tokens
        wallet.available_tokens += applied_amount
        entry, inserted = await ledger.insert_or_get(
            user_id=user_id,
            wallet_id=wallet.id,
            kind=kind,
            amount=applied_amount,
            balance_after_available=wallet.available_tokens,
            balance_after_reserved=wallet.reserved_tokens,
            ref_type=ref_type,
            ref_id=ref_id,
            meta=meta,
        )
        if clamped_shortfall > 0:
            await ledger.insert_or_get(
                user_id=user_id,
                wallet_id=wallet.id,
                kind=TokenLedgerKind.debit_adjustment,
                amount=-clamped_shortfall,
                balance_after_available=wallet.available_tokens,
                balance_after_reserved=wallet.reserved_tokens,
                ref_type=ref_type,
                ref_id=ref_id + ":clamp",
                meta={"reason": "refund_clamped_to_zero"},
            )
        return WalletOpResult(
            entry=entry,
            inserted=inserted,
            available=wallet.available_tokens,
            reserved=wallet.reserved_tokens,
        )

    @staticmethod
    async def _find_existing_entry(
        session: AsyncSession, key: str
    ) -> TokenLedgerEntry | None:
        from sqlalchemy import select

        result = await session.execute(
            select(TokenLedgerEntry).where(
                TokenLedgerEntry.idempotency_key == key
            )
        )
        return result.scalar_one_or_none()
