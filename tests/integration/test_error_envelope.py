from __future__ import annotations


async def test_error_envelope_has_request_id(app_client):
    r = await app_client.post(
        "/api/v1/chat/conversations", json={"title": "x"}, headers={}
    )
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "auth_error"
    assert "message" in body
    assert "requestId" in body
    rid = body["requestId"]
    assert rid is None or isinstance(rid, str)
    assert "x-request-id" in {k.lower() for k in r.headers.keys()}


async def test_request_id_is_propagated_from_header(app_client):
    r = await app_client.post(
        "/api/v1/chat/conversations",
        json={"title": "x"},
        headers={"X-Request-Id": "test-rid-123"},
    )
    assert r.headers.get("x-request-id") == "test-rid-123"
    body = r.json()
    if r.status_code >= 400:
        assert body.get("requestId") == "test-rid-123"


async def test_validation_error_envelope(app_client):
    r = await app_client.post("/api/v1/chat/messages", json={"message": ""})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "validation_error"
    assert "details" in body
