from __future__ import annotations


async def test_healthz_is_public(app_client):
    r = await app_client.get("/healthz", headers={})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_missing_authorization_returns_401(app_client):
    r = await app_client.post(
        "/api/v1/chat/conversations", json={"title": "x"}, headers={}
    )
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "auth_error"
    assert "requestId" in body


async def test_invalid_bearer_returns_401(app_client):
    r = await app_client.post(
        "/api/v1/chat/conversations",
        json={"title": "x"},
        headers={"Authorization": "Bearer wrongkey"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "auth_error"


async def test_criteria_requires_auth(app_client):
    r = await app_client.get("/api/v1/word-tools/criteria", headers={})
    assert r.status_code == 401
