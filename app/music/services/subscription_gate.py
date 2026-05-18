"""Подписка: ensure_active с lazy-expiry.

Если состояние active, но `expires_at <= now()`, переводим в `expired`,
замораживаем кошелёк (`wallet.frozen = true`) и бросаем
`SubscriptionExpired`. Для status=none/canceled-after-expiry — `SubscriptionRequired`.
Изменения коммитятся ДО исключения, иначе `async with session.begin()`
откатит lazy-expire.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import SubscriptionExpired, SubscriptionRequired
from app.music.enums import SubscriptionStatus
from app.music.models import SubscriptionState
from app.music.repositories.subscriptions import SubscriptionsRepository
from app.music.repositories.wallets import WalletsRepository

logger = logging.getLogger(__name__)


class SubscriptionGate:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def ensure_active(
        self, user_id, *, now: datetime | None = None
    ) -> SubscriptionState:
        now = now or datetime.now(tz=timezone.utc)
        expired_now = False
        reason: str | None = None
        async with self._sessionmaker() as session:
            async with session.begin():
                subs = SubscriptionsRepository(session)
                state = await subs.ensure_exists(user_id)
                if state.status == SubscriptionStatus.active:
                    if state.expires_at is None or state.expires_at <= now:
                        state.status = SubscriptionStatus.expired
                        wallets = WalletsRepository(session)
                        wallet = await wallets.ensure_exists(user_id)
                        wallet.frozen = True
                        expired_now = True
                        reason = "expired"
                    else:
                        return state
                elif (
                    state.status == SubscriptionStatus.canceled
                    and state.expires_at is not None
                    and state.expires_at > now
                ):
                    return state
                else:
                    reason = state.status.value
        if expired_now:
            logger.info(
                "Subscription expired on access",
                extra={"user_id": str(user_id)},
            )
        # SUBSCRIPTION_EXPIRED для истёкших, SUBSCRIPTION_REQUIRED для остальных.
        if reason == "expired" or reason == SubscriptionStatus.expired.value:
            raise SubscriptionExpired(details={"reason": reason})
        raise SubscriptionRequired(details={"reason": reason})
