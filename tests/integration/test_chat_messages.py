from __future__ import annotations

from app.api.errors import LLMProviderError, LLMTimeout


async def test_send_message_auto_creates_conversation(app_client, fake_llm):
    fake_llm.chat_response = "Hello, human."
    r = await app_client.post("/api/v1/chat/messages", json={"message": "Hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["assistantText"] == "Hello, human."
    assert body["conversationId"]
    assert body["userMessageId"]
    assert body["assistantMessageId"]


async def test_send_message_continues_existing_conversation(app_client, fake_llm):
    fake_llm.chat_response = "First reply."
    r1 = await app_client.post("/api/v1/chat/messages", json={"message": "Hi"})
    conv_id = r1.json()["conversationId"]
    fake_llm.chat_response = "Second reply."
    r2 = await app_client.post(
        "/api/v1/chat/messages",
        json={"message": "And again", "conversationId": conv_id},
    )
    assert r2.status_code == 200
    assert r2.json()["conversationId"] == conv_id
    assert len(fake_llm.chat_calls) == 2
    last = fake_llm.chat_calls[-1]["messages"]
    user_msgs = [m for m in last if m["role"] == "user"]
    assert any(m["content"] == "Hi" for m in user_msgs)
    assert any(m["content"] == "And again" for m in user_msgs)


async def test_send_message_empty_returns_400(app_client):
    r = await app_client.post("/api/v1/chat/messages", json={"message": "   "})
    assert r.status_code == 400
    assert r.json()["code"] in {"validation_error"}


async def test_send_message_missing_field_returns_400(app_client):
    r = await app_client.post("/api/v1/chat/messages", json={})
    assert r.status_code == 400


async def test_send_message_to_unknown_conversation_returns_404(app_client):
    r = await app_client.post(
        "/api/v1/chat/messages",
        json={
            "message": "Hi",
            "conversationId": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert r.status_code == 404
    assert r.json()["code"] == "conversation_not_found"


async def test_llm_timeout_returns_504(app_client, fake_llm):
    fake_llm.chat_response = LLMTimeout()
    r = await app_client.post("/api/v1/chat/messages", json={"message": "Hi"})
    assert r.status_code == 504
    assert r.json()["code"] == "llm_timeout"


async def test_llm_provider_error_returns_502(app_client, fake_llm):
    fake_llm.chat_response = LLMProviderError("oops")
    r = await app_client.post("/api/v1/chat/messages", json={"message": "Hi"})
    assert r.status_code == 502
    assert r.json()["code"] == "llm_provider_error"
