from __future__ import annotations

from app.api.errors import LLMProviderError, LLMTimeout


async def test_search_happy_path(app_client, fake_llm):
    fake_llm.json_response = {
        "items": [
            {"text": "dove", "score": 0.95},
            {"text": "glove", "score": 0.9},
        ],
        "total": 2,
    }
    r = await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "love", "criterion": "rhymes", "limit": 10, "offset": 0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "love"
    assert body["criterion"] == "rhymes"
    assert body["total"] == 2
    assert [i["text"] for i in body["items"]] == ["dove", "glove"]
    assert body["promptVersion"]


async def test_search_unknown_criterion_returns_400(app_client):
    r = await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "love", "criterion": "bogus", "limit": 10, "offset": 0},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "validation_error"


async def test_search_unscramble_short_query_returns_422(app_client):
    r = await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "a", "criterion": "unscramble", "limit": 10, "offset": 0},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_query_for_criterion"


async def test_search_llm_provider_error_returns_502(app_client, fake_llm):
    fake_llm.json_response = LLMProviderError("bad shape")
    r = await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "love", "criterion": "rhymes", "limit": 5, "offset": 0},
    )
    assert r.status_code == 502
    assert r.json()["code"] == "llm_provider_error"


async def test_search_llm_timeout_returns_504(app_client, fake_llm):
    fake_llm.json_response = LLMTimeout()
    r = await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "love", "criterion": "rhymes", "limit": 5, "offset": 0},
    )
    assert r.status_code == 504
    assert r.json()["code"] == "llm_timeout"


async def test_search_offset_and_limit(app_client, fake_llm):
    fake_llm.json_response = {
        "items": [{"text": f"w{i}", "score": 1 - 0.01 * i} for i in range(10)],
        "total": 10,
    }
    r = await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "love", "criterion": "rhymes", "limit": 3, "offset": 2},
    )
    body = r.json()
    assert [i["text"] for i in body["items"]] == ["w2", "w3", "w4"]
    assert body["total"] == 10


async def test_search_writes_analytics(app_client, db_session, fake_llm):
    from sqlalchemy import select

    from app.models import SearchRequest

    fake_llm.json_response = {"items": [{"text": "dove", "score": 1.0}], "total": 1}
    await app_client.post(
        "/api/v1/word-tools/search",
        json={"query": "love", "criterion": "rhymes", "limit": 5, "offset": 0},
    )
    rows = (await db_session.execute(select(SearchRequest))).scalars().all()
    assert len(rows) == 1
    assert rows[0].query == "love"
    assert rows[0].criterion == "rhymes"
    assert rows[0].result_count == 1
