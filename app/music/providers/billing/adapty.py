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


_EVENT_MAP: dict[str, BillingEventKind] = {
    "subscription_started": BillingEventKind.subscription_purchased,
    "subscription_renewed": BillingEventKind.subscription_renewed,
    "subscription_cancelled": BillingEventKind.subscription_canceled,
    "subscription_expired": BillingEventKind.subscription_expired,
    "non_subscription_purchase": BillingEventKind.one_time_purchase,
    "refund": BillingEventKind.refund,
}


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

    event_type = (data.get("event_type") or "").lower()
    if event_type not in _EVENT_MAP:
        raise WebhookPayloadInvalid(
            details={"reason": "unknown_event_type", "event_type": event_type}
        )
    kind = _EVENT_MAP[event_type]

    event_id = (
        data.get("event_id")
        or data.get("idempotency_key")
        or data.get("id")
    )
    if not event_id:
        raise WebhookPayloadInvalid(details={"reason": "no_event_id"})

    profile_id = (
        data.get("profile_id")
        or data.get("user_id")
        or data.get("customer_user_id")
    )
    if not profile_id:
        raise WebhookPayloadInvalid(details={"reason": "no_profile_id"})

    product_id = data.get("vendor_product_id") or data.get("product_id")
    token_amount = data.get("token_amount")
    occurred_at = _parse_dt(data.get("event_datetime") or data.get("occurred_at"))
    expires_at = _parse_dt_optional(
        data.get("expires_at") or data.get("subscription_expires_at")
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
