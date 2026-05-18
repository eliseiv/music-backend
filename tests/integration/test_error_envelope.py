"""Формат ошибок: ТЗ §13 — `{"error": {"code", "message", "details"}, "requestId": "..."}`."""
from __future__ import annotations


async def test_error_envelope_shape(app_client, auth_headers):
    headers = auth_headers()
    headers.pop("X-User-Id", None)
    r = await app_client.get("/v1/beats", headers=headers)
    body = r.json()
    assert "error" in body
    assert "requestId" in body
    assert set(body["error"].keys()) >= {"code", "message"}
    # детали опциональны
    assert body["error"]["code"] == "MISSING_X_USER_ID"


async def test_error_codes_are_upper_snake(app_client):
    """ТЗ §13: коды UPPER_SNAKE_CASE."""
    # 401 при отсутствии Bearer
    r = await app_client.get(
        "/v1/beats",
        headers={"Authorization": "", "X-User-Id": "u1"},
    )
    code = r.json()["error"]["code"]
    assert code == code.upper()
    assert "_" in code or code.isupper()


async def test_request_id_propagated_from_header(app_client, auth_headers):
    rid = "test-req-12345"
    headers = auth_headers()
    headers["X-Request-Id"] = rid
    r = await app_client.get(
        "/v1/tokens/balance", headers=headers
    )
    assert r.status_code == 200
    assert r.headers.get("X-Request-Id") == rid


async def test_validation_error_format(app_client, auth_headers, seed_beats, seed_pricing):
    """422 при invalid body превращается в INVALID_INPUT."""
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers(),
        json={"beatId": "not-uuid"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "INVALID_INPUT"
    assert "errors" in body["error"]["details"]
