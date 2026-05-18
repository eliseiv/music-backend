from __future__ import annotations

import hashlib
import hmac
from typing import Mapping

from app.api.errors import WebhookSignatureInvalid

SIGNATURE_HEADER = "X-Fal-Signature"


def compute_signature(secret: str, raw_body: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()


def verify_signature(
    *, secret: str, raw_body: bytes, headers: Mapping[str, str]
) -> None:
    if not secret:
        raise WebhookSignatureInvalid(
            details={"reason": "secret_not_configured"}
        )
    received = headers.get(SIGNATURE_HEADER) or headers.get(
        SIGNATURE_HEADER.lower()
    )
    if not received:
        raise WebhookSignatureInvalid(details={"reason": "header_missing"})
    expected = compute_signature(secret, raw_body)
    if not hmac.compare_digest(received.strip(), expected):
        raise WebhookSignatureInvalid(details={"reason": "mismatch"})


def body_digest(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()
