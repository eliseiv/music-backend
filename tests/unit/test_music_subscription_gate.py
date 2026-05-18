from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.errors import SubscriptionInactive
from app.music.enums import SubscriptionStatus
from app.music.repositories.subscriptions import SubscriptionsRepository
from app.music.repositories.users import MusicUsersRepository
from app.music.repositories.wallets import WalletsRepository
from app.music.services.subscription_gate import SubscriptionGate


@pytest_asyncio.fixture
async def _truncate_music(engine):
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE subscription_state, token_ledger, "
            "token_wallets, music_users RESTART IDENTITY CASCADE"
        )


@pytest_asyncio.fixture
async def gate(engine, _truncate_music):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return SubscriptionGate(sm)


@pytest_asyncio.fixture
async def music_user_id(engine, _truncate_music):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        async with session.begin():
            user = await MusicUsersRepository(session).get_or_create(
                external_id=f"sub-{uuid4()}"
            )
        return user.id


async def _set_state(
    engine, user_id, status: SubscriptionStatus, *, expires_at=None
):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        async with session.begin():
            subs = SubscriptionsRepository(session)
            state = await subs.ensure_exists(user_id)
            state.status = status
            state.expires_at = expires_at


async def test_none_status_blocked(gate, music_user_id):
    with pytest.raises(SubscriptionInactive):
        await gate.ensure_active(music_user_id)


async def test_active_with_future_expiry_passes(gate, engine, music_user_id):
    future = datetime.now(tz=timezone.utc) + timedelta(days=10)
    await _set_state(
        engine, music_user_id, SubscriptionStatus.active, expires_at=future
    )
    state = await gate.ensure_active(music_user_id)
    assert state.status == SubscriptionStatus.active


async def test_active_past_expiry_lazy_expires_and_freezes(
    gate, engine, music_user_id
):
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
    await _set_state(
        engine, music_user_id, SubscriptionStatus.active, expires_at=past
    )
    with pytest.raises(SubscriptionInactive):
        await gate.ensure_active(music_user_id)
    # Wallet must be frozen now
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        async with session.begin():
            wallet = await WalletsRepository(session).ensure_exists(
                music_user_id
            )
            assert wallet.frozen is True


async def test_canceled_with_future_expiry_passes(gate, engine, music_user_id):
    future = datetime.now(tz=timezone.utc) + timedelta(days=2)
    await _set_state(
        engine, music_user_id, SubscriptionStatus.canceled, expires_at=future
    )
    state = await gate.ensure_active(music_user_id)
    assert state.status == SubscriptionStatus.canceled


async def test_canceled_past_expiry_blocked(gate, engine, music_user_id):
    past = datetime.now(tz=timezone.utc) - timedelta(days=1)
    await _set_state(
        engine, music_user_id, SubscriptionStatus.canceled, expires_at=past
    )
    with pytest.raises(SubscriptionInactive):
        await gate.ensure_active(music_user_id)


async def test_expired_status_blocked(gate, engine, music_user_id):
    await _set_state(engine, music_user_id, SubscriptionStatus.expired)
    with pytest.raises(SubscriptionInactive):
        await gate.ensure_active(music_user_id)
