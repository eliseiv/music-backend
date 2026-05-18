from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.errors import InsufficientTokens, SubscriptionInactive
from app.music.enums import TokenLedgerKind
from app.music.repositories.users import MusicUsersRepository
from app.music.services.wallet_service import WalletService


@pytest_asyncio.fixture
async def wallet_service(engine, _truncate_music):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return WalletService(sm)


@pytest_asyncio.fixture
async def music_user_id(engine, _truncate_music):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        async with session.begin():
            repo = MusicUsersRepository(session)
            user = await repo.get_or_create(external_id=f"test-{uuid4()}")
        return user.id


@pytest_asyncio.fixture
async def _truncate_music(engine):
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE token_ledger, token_wallets, music_users "
            "RESTART IDENTITY CASCADE"
        )


async def test_reserve_from_empty_wallet_raises_insufficient(
    wallet_service, music_user_id
):
    with pytest.raises(InsufficientTokens):
        await wallet_service.reserve(
            user_id=music_user_id, amount=5, ref_type="job", ref_id="j1"
        )


async def test_reserve_capture_release_cycle(wallet_service, music_user_id):
    # Сначала наполним кошелёк через credit
    await wallet_service.credit(
        user_id=music_user_id,
        amount=10,
        kind=TokenLedgerKind.credit_purchase,
        ref_type="purchase",
        ref_id="p1",
    )
    bal = await wallet_service.get_balance(music_user_id)
    assert bal.available == 10 and bal.reserved == 0

    # Reserve 3
    res = await wallet_service.reserve(
        user_id=music_user_id, amount=3, ref_type="job", ref_id="j1"
    )
    assert res.inserted is True
    assert res.available == 7 and res.reserved == 3

    # Capture 2 (release 1 back)
    cap = await wallet_service.capture(
        user_id=music_user_id,
        amount=2,
        previously_reserved=3,
        ref_type="job",
        ref_id="j1",
    )
    assert cap.inserted is True
    assert cap.available == 8 and cap.reserved == 0


async def test_reserve_idempotent(wallet_service, music_user_id):
    await wallet_service.credit(
        user_id=music_user_id,
        amount=5,
        kind=TokenLedgerKind.credit_purchase,
        ref_type="purchase",
        ref_id="p1",
    )
    r1 = await wallet_service.reserve(
        user_id=music_user_id, amount=2, ref_type="job", ref_id="j1"
    )
    r2 = await wallet_service.reserve(
        user_id=music_user_id, amount=2, ref_type="job", ref_id="j1"
    )
    assert r1.inserted is True and r2.inserted is False
    assert r1.entry.id == r2.entry.id
    bal = await wallet_service.get_balance(music_user_id)
    # Only one debit happened; available reflects single reserve.
    assert bal.available == 3 and bal.reserved == 2


async def test_release_returns_reserved_to_available(wallet_service, music_user_id):
    await wallet_service.credit(
        user_id=music_user_id,
        amount=5,
        kind=TokenLedgerKind.credit_purchase,
        ref_type="purchase",
        ref_id="p1",
    )
    await wallet_service.reserve(
        user_id=music_user_id, amount=3, ref_type="job", ref_id="j1"
    )
    rel = await wallet_service.release(
        user_id=music_user_id, amount=3, ref_type="job", ref_id="j1"
    )
    assert rel.available == 5 and rel.reserved == 0


async def test_capture_works_on_frozen_wallet(wallet_service, music_user_id):
    """Capture must succeed even on frozen wallet — tokens are already reserved."""
    await wallet_service.credit(
        user_id=music_user_id,
        amount=5,
        kind=TokenLedgerKind.credit_purchase,
        ref_type="purchase",
        ref_id="p1",
    )
    await wallet_service.reserve(
        user_id=music_user_id, amount=2, ref_type="job", ref_id="j1"
    )
    await wallet_service.set_frozen(user_id=music_user_id, frozen=True)
    cap = await wallet_service.capture(
        user_id=music_user_id,
        amount=2,
        previously_reserved=2,
        ref_type="job",
        ref_id="j1",
    )
    assert cap.inserted is True


async def test_reserve_blocked_on_frozen_wallet(wallet_service, music_user_id):
    await wallet_service.credit(
        user_id=music_user_id,
        amount=5,
        kind=TokenLedgerKind.credit_purchase,
        ref_type="purchase",
        ref_id="p1",
    )
    await wallet_service.set_frozen(user_id=music_user_id, frozen=True)
    with pytest.raises(SubscriptionInactive):
        await wallet_service.reserve(
            user_id=music_user_id, amount=1, ref_type="job", ref_id="j2"
        )


async def test_refund_clamps_at_zero(wallet_service, music_user_id):
    await wallet_service.credit(
        user_id=music_user_id,
        amount=3,
        kind=TokenLedgerKind.credit_purchase,
        ref_type="purchase",
        ref_id="p1",
    )
    # Refund 5 — больше чем есть
    await wallet_service.credit(
        user_id=music_user_id,
        amount=-5,
        kind=TokenLedgerKind.credit_refund,
        ref_type="refund",
        ref_id="r1",
    )
    bal = await wallet_service.get_balance(music_user_id)
    assert bal.available == 0  # clamped
