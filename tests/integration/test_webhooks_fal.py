"""POST /v1/webhooks/fal — подпись, идемпотентность."""
from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(app_client):
    r = await app_client.post(
        "/v1/webhooks/fal",
        content=b'{"request_id":"r","status":"completed"}',
        headers={
            "X-Fal-Signature": "wrong",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_missing_signature_returns_401(app_client):
    r = await app_client.post(
        "/v1/webhooks/fal",
        content=b'{"request_id":"r","status":"completed"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_duplicate_event_id_returns_duplicate(app_client, fake_fal):
    # Webhook для несуществующего job — но HMAC валидный.
    # Первый — должен вернуть ok, второй — duplicate (по event_id).
    resp1 = await fake_fal.emit_webhook(
        app_client,
        request_id="unknown-req",
        status="completed",
        event_id="dup-evt-1",
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "ok"
    resp2 = await fake_fal.emit_webhook(
        app_client,
        request_id="unknown-req",
        status="completed",
        event_id="dup-evt-1",
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "duplicate"
