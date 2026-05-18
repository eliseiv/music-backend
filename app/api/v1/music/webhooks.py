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

router = APIRouter(tags=["music-webhooks"])


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
    summary="Webhook от fal.ai",
    description=(
        "Принимает событие завершения генерации от fal.ai. "
        "Требует валидной подписи `X-Fal-Signature` (HMAC-SHA256 от raw body "
        "с секретом из `FAL_WEBHOOK_SECRET`). Идемпотентен по `event_id`."
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
    async with sessionmaker() as session:
        async with session.begin():
            repo = WebhooksRepository(session)
            recorded = await repo.try_record(
                provider=WebhookProvider.fal,
                event_id=event.event_id,
                payload_digest=event.payload_digest,
                outcome="applied",
            )
    if not recorded:
        return {"status": "duplicate"}
    await service.finalize_from_webhook(event)
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
    summary="Webhook от Adapty",
    description=(
        "Принимает событие подписки/покупки от Adapty.\n\n"
        "**Авторизация:** Adapty передаёт значение `ADAPTY_WEBHOOK_SECRET` "
        "в заголовке `Authorization` (с префиксом `Bearer` или без — оба "
        "варианта поддерживаются).\n\n"
        "При подключении хука Adapty шлёт тестовый POST с пустым телом — "
        "сервис вернёт 200 `test_ping` (после проверки Authorization)."
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
    summary="Webhook от RuStore (РФ биллинг)",
    description=(
        "Принимает событие подписки/покупки от РФ-биллинга. Подпись — "
        "HMAC-SHA256 в `X-RuStore-Signature` с секретом "
        "`RF_BILLING_WEBHOOK_SECRET`."
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
