from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Mapping

from app.api.errors import WebhookPayloadInvalid, WebhookSignatureInvalid
from app.music.enums import BillingEventKind, BillingProvider
from app.music.providers.billing.base import NormalizedBillingEvent
from app.music.providers.fal.signature import body_digest

SIGNATURE_HEADER = "X-RuStore-Signature"


_EVENT_MAP: dict[str, BillingEventKind] = {
    "SUBSCRIPTION_PURCHASED": BillingEventKind.subscription_purchased,
    "SUBSCRIPTION_RENEWED": BillingEventKind.subscription_renewed,
    "SUBSCRIPTION_CANCELED": BillingEventKind.subscription_canceled,
    "SUBSCRIPTION_EXPIRED": BillingEventKind.subscription_expired,
    "ONE_TIME_PURCHASE": BillingEventKind.one_time_purchase,
    "REFUND": BillingEventKind.refund,
}


def verify_signature(*, secret: str, raw_body: bytes, headers: Mapping[str, str]) -> None:
    if not secret:
        raise WebhookSignatureInvalid(details={"reason": "secret_not_configured"})
    received = headers.get(SIGNATURE_HEADER) or headers.get(
        SIGNATURE_HEADER.lower()
    )
    if not received:
        raise WebhookSignatureInvalid(details={"reason": "header_missing"})
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received.strip(), expected):
        raise WebhookSignatureInvalid(details={"reason": "mismatch"})


def parse_event(raw_body: bytes) -> NormalizedBillingEvent:
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookPayloadInvalid(details={"reason": "not_json"}) from exc
    if not isinstance(data, dict):
        raise WebhookPayloadInvalid(details={"reason": "not_object"})

    event_type = (data.get("event_type") or "").upper()
    if event_type not in _EVENT_MAP:
        raise WebhookPayloadInvalid(
            details={"reason": "unknown_event_type", "event_type": event_type}
        )
    kind = _EVENT_MAP[event_type]

    event_id = data.get("event_id") or data.get("id")
    if not event_id:
        raise WebhookPayloadInvalid(details={"reason": "no_event_id"})

    user_id = data.get("user_id") or data.get("external_user_id")
    if not user_id:
        raise WebhookPayloadInvalid(details={"reason": "no_user_id"})

    product_id = data.get("product_id")
    token_amount = data.get("token_amount")
    occurred_at = _parse_dt(data.get("occurred_at") or data.get("timestamp"))
    expires_at = _parse_dt_optional(data.get("expires_at"))

    return NormalizedBillingEvent(
        provider=BillingProvider.rustore,
        event_id=str(event_id),
        kind=kind,
        external_user_id=str(user_id),
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
