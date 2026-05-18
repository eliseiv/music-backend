from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.music._common import MUSIC_ERROR_RESPONSES
from app.config import Settings
from app.deps import (
    get_pricing_service,
    get_sessionmaker,
    get_settings_dep,
    get_subscription_gate,
    get_wallet_service,
)
from app.music.enums import WebhookProvider
from app.music.providers.billing import adapty as adapty_parser
from app.music.providers.billing import rf as rf_parser
from app.music.providers.fal.base import FalProvider
from app.music.repositories.webhooks import WebhooksRepository
from app.music.services.generation_service import GenerationService
from app.music.services.pricing_service import PricingService
from app.music.services.subscription_gate import SubscriptionGate
from app.music.services.wallet_service import WalletService
from app.music.services.webhook_billing import BillingWebhookService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Webhooks"])


def _get_fal_provider(request: Request) -> FalProvider:
    from fastapi import HTTPException

    provider = getattr(request.app.state, "fal_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="fal provider is not configured (set FAL_API_KEY in .env)",
        )
    return provider


def _get_generation_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
    fal: Annotated[FalProvider, Depends(_get_fal_provider)],
    wallet: Annotated[WalletService, Depends(get_wallet_service)],
    pricing: Annotated[PricingService, Depends(get_pricing_service)],
    gate: Annotated[SubscriptionGate, Depends(get_subscription_gate)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> GenerationService:
    return GenerationService(sessionmaker, fal, wallet, pricing, gate, settings)


@router.post(
    "/webhooks/fal",
    status_code=status.HTTP_200_OK,
    summary="Webhook от fal.ai (завершение стадии генерации)",
    description=(
        "Принимает событие завершения async-стадии пайплайна от fal.ai "
        "(`music_generation`, `audio_to_audio_refine`, `vocal_tts`).\n\n"
        "**Авторизация:** HMAC-SHA256 от raw body в заголовке "
        "`X-Fal-Signature`, секрет — `FAL_WEBHOOK_SECRET` из `.env`. "
        "Без подписи → `401 WEBHOOK_SIGNATURE_INVALID`.\n\n"
        "**Идемпотентность:** по `(provider, event_id)` в "
        "`processed_webhooks`. Повторный webhook возвращает "
        "`{\"status\": \"duplicate\"}` без эффекта.\n\n"
        "**2-фазная обработка**: сначала `outcome=received`, "
        "после успешного применения — `applied`. При падении посередине "
        "событие останется в `received` и попадёт в recovery sweep при старте."
    ),
    responses={
        k: v
        for k, v in MUSIC_ERROR_RESPONSES.items()
        if k in {400, 401}
    },
)
async def fal_webhook(
    request: Request,
    fal: Annotated[FalProvider, Depends(_get_fal_provider)],
    service: Annotated[GenerationService, Depends(_get_generation_service)],
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> dict[str, str]:
    raw = await request.body()
    event = fal.parse_webhook_event(headers=request.headers, raw_body=raw)
    # Phase 1: claim event (outcome="received") для 2-фазной обработки.
    async with sessionmaker() as session:
        async with session.begin():
            repo = WebhooksRepository(session)
            recorded = await repo.try_record(
                provider=WebhookProvider.fal,
                event_id=event.event_id,
                payload_digest=event.payload_digest,
                outcome="received",
            )
    if not recorded:
        return {"status": "duplicate"}
    # Phase 2: применить пайплайн, при успехе пометить applied.
    await service.finalize_from_webhook(event)
    async with sessionmaker() as session:
        async with session.begin():
            await WebhooksRepository(session).mark_applied(
                provider=WebhookProvider.fal, event_id=event.event_id
            )
    return {"status": "ok"}


def _get_billing_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
    wallet: Annotated[WalletService, Depends(get_wallet_service)],
) -> BillingWebhookService:
    return BillingWebhookService(sessionmaker, wallet)


@router.post(
    "/webhooks/billing/adapty",
    status_code=status.HTTP_200_OK,
    summary="Webhook от Adapty (App Store / Google Play)",
    description=(
        "Принимает события подписки и покупок токен-паков от Adapty:\n\n"
        "* `subscription_started` → `SUBSCRIPTION_PURCHASED` "
        "(активация подписки, размораживание кошелька, кредит токенов).\n"
        "* `subscription_renewed` → `SUBSCRIPTION_RENEWED`.\n"
        "* `subscription_cancelled` → `SUBSCRIPTION_CANCELED` "
        "(доступ до конца периода).\n"
        "* `subscription_expired` → `SUBSCRIPTION_EXPIRED` "
        "(`wallet.frozen=true`, токены сохраняются).\n"
        "* `non_subscription_purchase` → `ONE_TIME_PURCHASE` "
        "(резолв количества токенов через `token_products`).\n"
        "* `refund` → `REFUND` (debit с clamp в 0).\n\n"
        "**Авторизация:** значение `ADAPTY_WEBHOOK_SECRET` в заголовке "
        "`Authorization` (поддерживаются оба формата: `Bearer <secret>` "
        "и просто `<secret>`).\n\n"
        "**Идемпотентность:** по `event_id` (повторное событие → "
        "`{\"status\": \"duplicate\"}`).\n\n"
        "**Атомарность:** user, subscription_state, wallet, ledger и "
        "processed_webhooks применяются в одной транзакции."
    ),
    responses={
        k: v
        for k, v in MUSIC_ERROR_RESPONSES.items()
        if k in {400, 401}
    },
)
async def adapty_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[BillingWebhookService, Depends(_get_billing_service)],
) -> dict[str, str]:
    adapty_parser.verify_authorization(
        secret=settings.ADAPTY_WEBHOOK_SECRET.get_secret_value(),
        headers=request.headers,
    )
    raw = await request.body()
    # Adapty при подключении хука шлёт пустой POST — это test-ping.
    if not raw.strip():
        return {"status": "test_ping"}
    # Явный test-event {"event_type":"test"}.
    if _is_test_payload(raw):
        return {"status": "test_ping"}
    event = adapty_parser.parse_event(raw)
    outcome = await service.apply(event)
    return {"status": outcome}


def _is_test_payload(raw: bytes) -> bool:
    import json

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    event_type = (data.get("event_type") or "").lower()
    return event_type in {"test", "ping", "test_event"}


@router.post(
    "/webhooks/billing/rf",
    status_code=status.HTTP_200_OK,
    summary="Webhook от RuStore",
    description=(
        "Принимает события подписки и покупок от РФ-биллинга (RuStore):\n\n"
        "* `SUBSCRIPTION_PURCHASED` — активация подписки.\n"
        "* `SUBSCRIPTION_RENEWED` — продление.\n"
        "* `SUBSCRIPTION_CANCELED` — отмена.\n"
        "* `SUBSCRIPTION_EXPIRED` — истечение, кошелёк frozen.\n"
        "* `ONE_TIME_PURCHASE` — покупка токен-пака.\n"
        "* `REFUND` — возврат.\n\n"
        "**Авторизация:** HMAC-SHA256 от raw body в заголовке "
        "`X-RuStore-Signature`, секрет — `RF_BILLING_WEBHOOK_SECRET` "
        "из `.env`. Без подписи → `401 WEBHOOK_SIGNATURE_INVALID`.\n\n"
        "**Test-ping:** пустой POST → `200 {\"status\": \"test_ping\"}` "
        "(подпись для пустого body не проверяется).\n\n"
        "**Идемпотентность + атомарность** — как у Adapty."
    ),
    responses={
        k: v
        for k, v in MUSIC_ERROR_RESPONSES.items()
        if k in {400, 401}
    },
)
async def rf_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[BillingWebhookService, Depends(_get_billing_service)],
) -> dict[str, str]:
    raw = await request.body()
    if not raw.strip():
        return {"status": "test_ping"}
    rf_parser.verify_signature(
        secret=settings.RF_BILLING_WEBHOOK_SECRET.get_secret_value(),
        raw_body=raw,
        headers=request.headers,
    )
    if _is_test_payload(raw):
        return {"status": "test_ping"}
    event = rf_parser.parse_event(raw)
    outcome = await service.apply(event)
    return {"status": outcome}
