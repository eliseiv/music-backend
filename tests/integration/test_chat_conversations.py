from __future__ import annotations


async def test_create_conversation_with_title(app_client):
    r = await app_client.post("/api/v1/chat/conversations", json={"title": "First"})
    assert r.status_code == 201
    body = r.json()
    assert "conversationId" in body
    assert "createdAt" in body


async def test_create_conversation_without_title(app_client):
    r = await app_client.post("/api/v1/chat/conversations", json={})
    assert r.status_code == 201


async def test_create_conversation_title_too_long(app_client):
    r = await app_client.post(
        "/api/v1/chat/conversations", json={"title": "a" * 201}
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation_error"
