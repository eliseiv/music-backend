"""Auth / X-User-Id header — базовая защита всех music-эндпоинтов."""
from __future__ import annotations

import pytest


async def test_healthz_public(app_client):
    r = await app_client.get("/healthz", headers={"Authorization": ""})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_missing_bearer_returns_unauthorized(app_client):
    r = await app_client.get(
        "/v1/beats",
        headers={"Authorization": "", "X-User-Id": "u1"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "UNAUTHORIZED"


async def test_wrong_bearer_returns_unauthorized(app_client):
    r = await app_client.get(
        "/v1/beats",
        headers={"Authorization": "Bearer wrong", "X-User-Id": "u1"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_missing_x_user_id_returns_400(app_client, auth_headers):
    headers = auth_headers()
    headers.pop("X-User-Id", None)
    r = await app_client.get("/v1/beats", headers=headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MISSING_X_USER_ID"


async def test_empty_x_user_id_returns_400(app_client):
    r = await app_client.get(
        "/v1/beats",
        headers={"Authorization": "Bearer testkey", "X-User-Id": "   "},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "MISSING_X_USER_ID"
