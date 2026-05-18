from __future__ import annotations

import hashlib
import hmac

import pytest

from app.api.errors import WebhookSignatureInvalid
from app.music.providers.fal.signature import (
    SIGNATURE_HEADER,
    compute_signature,
    verify_signature,
)


SECRET = "topsecret"
BODY = b'{"request_id":"abc","status":"completed"}'


def _sig(body: bytes = BODY, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_valid_signature_passes():
    verify_signature(
        secret=SECRET, raw_body=BODY, headers={SIGNATURE_HEADER: _sig()}
    )


def test_verify_invalid_signature_rejected():
    with pytest.raises(WebhookSignatureInvalid):
        verify_signature(
            secret=SECRET, raw_body=BODY, headers={SIGNATURE_HEADER: "wrong"}
        )


def test_verify_missing_header_rejected():
    with pytest.raises(WebhookSignatureInvalid):
        verify_signature(secret=SECRET, raw_body=BODY, headers={})


def test_verify_empty_secret_rejected():
    with pytest.raises(WebhookSignatureInvalid):
        verify_signature(
            secret="", raw_body=BODY, headers={SIGNATURE_HEADER: _sig()}
        )


def test_compute_signature_matches_hmac():
    assert compute_signature(SECRET, BODY) == _sig()


def test_modified_body_signature_fails():
    with pytest.raises(WebhookSignatureInvalid):
        verify_signature(
            secret=SECRET,
            raw_body=BODY + b" tampered",
            headers={SIGNATURE_HEADER: _sig()},
        )
