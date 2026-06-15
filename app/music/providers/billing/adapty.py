"""Adapty webhook parser.

Adapty подключает webhook с **bearer-секретом в заголовке `Authorization`**:
- настраивается в Adapty Dashboard → Integrations → Webhook
- значение из `ADAPTY_WEBHOOK_SECRET` приходит как `Authorization: <secret>`
  (можно с префиксом `Bearer` или без — поддерживаем оба варианта).

Событие приходит как JSON с полями:
- event_type (subscription_started|subscription_renewed|subscription_cancelled
  |subscription_expired|non_subscription_purchase|refund)
- event_datetime
- profile_id          → external_user_id
- vendor_product_id, product_id
- expires_at
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Mapping

from app.api.errors import WebhookPayloadInvalid, WebhookSignatureInvalid
from app.music.enums import BillingEventKind, BillingProvider
from app.music.providers.billing.base import NormalizedBillingEvent
from app.music.providers.fal.signature import body_digest

AUTH_HEADER = "Authorization"


# Прямой маппинг имён событий (для старого формата / ручных активаций / тестов).
# Реальные события Adapty определяются в первую очередь по признаку доступа
# (is_active / profile_has_access_level) — см. _resolve_kind.
_EVENT_MAP: dict[str, BillingEventKind] = {
    "subscription_started": BillingEventKind.subscription_purchased,
    "subscription_initial_purchase": BillingEventKind.subscription_purchased,
    "subscription_renewed": BillingEventKind.subscription_renewed,
    "trial_started": BillingEventKind.subscription_purchased,
    "trial_converted": BillingEventKind.subscription_renewed,
    "subscription_cancelled": BillingEventKind.subscription_canceled,
    "subscription_renewal_cancelled": BillingEventKind.subscription_canceled,
    "trial_renewal_cancelled": BillingEventKind.subscription_canceled,
    "subscription_expired": BillingEventKind.subscription_expired,
    "trial_expired": BillingEventKind.subscription_expired,
    "non_subscription_purchase": BillingEventKind.one_time_purchase,
    "refund": BillingEventKind.refund,
    "subscription_refunded": BillingEventKind.refund,
}


def _resolve_kind(
    event_type: str, props: dict, top: dict
) -> BillingEventKind | None:
    """Определяет тип события. Приоритет — признак доступа из Adapty
    (is_active / profile_has_access_level): он отражает АКТУАЛЬНОЕ состояние
    подписки и устойчив к порядку/семантике событий (trial_renewal_cancelled
    с сохранённым доступом не должен «отключать» подписку). Фолбэк — маппинг
    по имени события (старый формат без event_properties).
    """
    # Рефанд — всегда рефанд, независимо от доступа.
    if event_type in ("refund", "subscription_refunded"):
        return BillingEventKind.refund
    if props.get("is_refund") is True or top.get("is_refund") is True:
        return BillingEventKind.refund
    if event_type == "non_subscription_purchase":
        return BillingEventKind.one_time_purchase

    # Признак доступа из Adapty — источник истины.
    access = props.get("is_active")
    if access is None:
        access = props.get("profile_has_access_level")
    if access is None:
        access = top.get("is_active")
    if access is None:
        access = top.get("profile_has_access_level")
    if access is True:
        return BillingEventKind.subscription_purchased
    if access is False:
        return BillingEventKind.subscription_expired

    # Фолбэк: маппинг по имени (старый формат / тесты без props).
    return _EVENT_MAP.get(event_type)


def verify_authorization(
    *, secret: str, headers: Mapping[str, str]
) -> None:
    """Проверка bearer-секрета в `Authorization`-заголовке."""
    if not secret:
        raise WebhookSignatureInvalid(details={"reason": "secret_not_configured"})
    received = headers.get(AUTH_HEADER) or headers.get(AUTH_HEADER.lower())
    if not received:
        raise WebhookSignatureInvalid(details={"reason": "header_missing"})
    received = received.strip()
    # Adapty может слать "<secret>" или "Bearer <secret>" — поддерживаем оба.
    if received.lower().startswith("bearer "):
        received = received[7:].strip()
    if not hmac.compare_digest(received, secret):
        raise WebhookSignatureInvalid(details={"reason": "mismatch"})


def parse_event(raw_body: bytes) -> NormalizedBillingEvent:
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookPayloadInvalid(details={"reason": "not_json"}) from exc
    if not isinstance(data, dict):
        raise WebhookPayloadInvalid(details={"reason": "not_object"})

    # Реальные события Adapty кладут все поля подписки во вложенный
    # event_properties. Старый формат / ручные активации / тесты — на верхнем
    # уровне. Ищем сначала в props, затем в data (top-level).
    props = data.get("event_properties") or {}
    if not isinstance(props, dict):
        props = {}

    def field(*keys):
        for k in keys:
            if props.get(k) is not None:
                return props[k]
            if data.get(k) is not None:
                return data[k]
        return None

    event_type = (data.get("event_type") or "").lower()

    kind = _resolve_kind(event_type, props, data)
    if kind is None:
        raise WebhookPayloadInvalid(
            details={"reason": "unknown_event_type", "event_type": event_type}
        )

    # event_id: profile_event_id (реальный Adapty) → event_id/id (старый формат)
    # → transaction_id+тип как последний фолбэк.
    event_id = field("profile_event_id", "event_id", "idempotency_key", "id")
    if not event_id:
        txid = field("transaction_id", "original_transaction_id")
        if txid:
            event_id = f"{txid}:{event_type}"
    if not event_id:
        raise WebhookPayloadInvalid(details={"reason": "no_event_id"})

    # profile_id — на верхнем уровне у Adapty (= external user id / X-User-Id).
    profile_id = (
        data.get("profile_id")
        or data.get("customer_user_id")
        or data.get("user_id")
        or props.get("profile_id")
    )
    if not profile_id:
        raise WebhookPayloadInvalid(details={"reason": "no_profile_id"})

    product_id = field("vendor_product_id", "product_id")
    token_amount = field("token_amount")
    occurred_at = _parse_dt(
        data.get("event_datetime") or field("event_datetime", "occurred_at")
    )
    expires_at = _parse_dt_optional(
        field("expires_at", "subscription_expires_at")
    )

    return NormalizedBillingEvent(
        provider=BillingProvider.adapty,
        event_id=str(event_id),
        kind=kind,
        external_user_id=str(profile_id),
        product_external_id=str(product_id) if product_id else None,
        token_amount=int(token_amount) if token_amount is not None else None,
        occurred_at=occurred_at,
        expires_at=expires_at,
        payload_digest=body_digest(raw_body),
        raw=data,
    )


def _parse_dt(value: str | None) -> datetime:
    if value is None:
        return datetime.now(tz=timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _parse_dt_optional(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_dt(value)
