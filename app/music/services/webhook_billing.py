from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.music.enums import (
    BillingEventKind,
    BillingPlatform,
    BillingProvider,
    SubscriptionStatus,
    TokenLedgerKind,
    WebhookProvider,
)
from app.music.models import TokenProduct
from app.music.providers.billing.base import NormalizedBillingEvent
from app.music.repositories.products import TokenProductsRepository
from app.music.repositories.subscriptions import SubscriptionsRepository
from app.music.repositories.users import MusicUsersRepository
from app.music.repositories.webhooks import WebhooksRepository
from app.music.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


_WEBHOOK_PROVIDER = {
    BillingProvider.adapty: WebhookProvider.adapty,
    BillingProvider.rustore: WebhookProvider.rustore,
}


class BillingWebhookService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        wallet: WalletService,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._wallet = wallet

    async def apply(self, event: NormalizedBillingEvent) -> str:
        """Returns 'applied' | 'duplicate'."""
        webhook_provider = _WEBHOOK_PROVIDER[event.provider]

        # --- Phase 1: try to claim event (outcome="received") ---
        async with self._sessionmaker() as session:
            async with session.begin():
                recorded = await WebhooksRepository(session).try_record(
                    provider=webhook_provider,
                    event_id=event.event_id,
                    payload_digest=event.payload_digest,
                    outcome="received",
                )
        if not recorded:
            return "duplicate"

        # --- Phase 2: единая транзакция — user, subscription, wallet ---
        async with self._sessionmaker() as session:
            async with session.begin():
                user = await MusicUsersRepository(session).get_or_create(
                    external_id=event.external_user_id
                )
                user_id = user.id

                subs = SubscriptionsRepository(session)
                state = await subs.ensure_exists(user_id)
                if (
                    state.last_event_occurred_at is not None
                    and state.last_event_occurred_at > event.occurred_at
                ):
                    logger.info(
                        "Older event %s for user=%s — applying conservatively",
                        event.event_id,
                        user_id,
                    )
                self._apply_to_state(state, event)
                state.last_event_id = event.event_id
                state.last_event_occurred_at = max(
                    state.last_event_occurred_at or event.occurred_at,
                    event.occurred_at,
                )
                await session.flush()

                await self._apply_wallet_effects(session, user_id, event)

                # mark applied внутри той же транзакции
                await WebhooksRepository(session).mark_applied(
                    provider=webhook_provider, event_id=event.event_id
                )

        return "applied"

    async def _apply_wallet_effects(
        self,
        session: AsyncSession,
        user_id,
        event: NormalizedBillingEvent,
    ) -> None:
        if event.kind in {
            BillingEventKind.subscription_purchased,
            BillingEventKind.subscription_renewed,
        }:
            await WalletService.set_frozen_in_session(
                session, user_id=user_id, frozen=False
            )
            # Сколько токенов начислить за подписку:
            #  - если событие явно несёт token_amount (старый формат / ручная
            #    активация) → используем его, идемпотентность по event_id;
            #  - иначе автоначисление по vendor_product_id через token_products,
            #    идемпотентность ПО ПЕРИОДУ подписки (product:expires_at), т.к.
            #    Adapty за один период шлёт несколько событий (access_level_updated,
            #    trial_started, trial_renewal_cancelled) с разными event_id —
            #    начислить надо один раз на период, а при renewal (новый
            #    expires_at) — снова.
            if event.token_amount and event.token_amount > 0:
                grant = event.token_amount
                ref_type, ref_id = "subscription_event", event.event_id
            else:
                grant = await self._resolve_tokens_for_product(
                    session=session, event=event
                )
                period = (
                    event.expires_at.isoformat()
                    if event.expires_at is not None
                    else event.event_id
                )
                ref_type = "subscription_period"
                ref_id = f"{event.product_external_id}:{period}"
            if grant and grant > 0:
                await WalletService.credit_in_session(
                    session,
                    user_id=user_id,
                    amount=grant,
                    kind=TokenLedgerKind.credit_subscription_grant,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    meta={"product": event.product_external_id},
                )
        elif event.kind == BillingEventKind.subscription_expired:
            await WalletService.set_frozen_in_session(
                session, user_id=user_id, frozen=True
            )
        elif event.kind == BillingEventKind.one_time_purchase:
            tokens_to_credit = await self._resolve_tokens_for_product(
                session=session, event=event
            )
            if tokens_to_credit > 0:
                await WalletService.credit_in_session(
                    session,
                    user_id=user_id,
                    amount=tokens_to_credit,
                    kind=TokenLedgerKind.credit_purchase,
                    ref_type="purchase",
                    ref_id=event.event_id,
                    meta={"product": event.product_external_id},
                )
        elif event.kind == BillingEventKind.refund:
            amount = (
                event.token_amount
                if event.token_amount is not None
                else await self._resolve_tokens_for_product(
                    session=session, event=event
                )
            )
            if amount > 0:
                await WalletService.credit_in_session(
                    session,
                    user_id=user_id,
                    amount=-amount,
                    kind=TokenLedgerKind.credit_refund,
                    ref_type="refund",
                    ref_id=event.event_id,
                    meta={"product": event.product_external_id},
                )

    @staticmethod
    async def _resolve_tokens_for_product(
        *, session: AsyncSession, event: NormalizedBillingEvent
    ) -> int:
        if not event.product_external_id:
            return 0
        platform = (
            BillingPlatform.adapty
            if event.provider == BillingProvider.adapty
            else BillingPlatform.rustore
        )
        products: list[TokenProduct] = await TokenProductsRepository(
            session
        ).list_active()
        for p in products:
            if (
                p.platform == platform
                and p.external_product_id == event.product_external_id
            ):
                return p.token_amount
        return 0

    @staticmethod
    def _apply_to_state(state, event: NormalizedBillingEvent) -> None:
        provider = (
            BillingProvider.adapty
            if event.provider == BillingProvider.adapty
            else BillingProvider.rustore
        )
        state.provider = provider
        if event.product_external_id:
            state.product_external_id = event.product_external_id

        if event.kind == BillingEventKind.subscription_purchased:
            state.status = SubscriptionStatus.active
            if state.started_at is None:
                state.started_at = event.occurred_at
            if event.expires_at is not None:
                state.expires_at = _max_dt(state.expires_at, event.expires_at)
        elif event.kind == BillingEventKind.subscription_renewed:
            state.status = SubscriptionStatus.active
            if event.expires_at is not None:
                state.expires_at = _max_dt(state.expires_at, event.expires_at)
        elif event.kind == BillingEventKind.subscription_canceled:
            state.status = SubscriptionStatus.canceled
            state.canceled_at = event.occurred_at
            # expires_at оставляем — пользователь имеет доступ до конца периода
        elif event.kind == BillingEventKind.subscription_expired:
            state.status = SubscriptionStatus.expired
            state.expires_at = state.expires_at or event.occurred_at


def _max_dt(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b
