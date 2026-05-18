"""POST /v1/tracks/generate — golden path и edge cases (ТЗ §3, §6, §15)."""
from __future__ import annotations

import pytest

from tests.integration.conftest import build_generate_payload


@pytest.mark.asyncio
async def test_generate_no_subscription_blocks(
    app_client, auth_headers, seed_beats, seed_pricing
):
    payload = build_generate_payload(seed_beats)
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers("no-sub-user"),
        json=payload,
    )
    assert r.status_code == 402
    body = r.json()
    assert body["error"]["code"] == "SUBSCRIPTION_REQUIRED"


@pytest.mark.asyncio
async def test_generate_insufficient_tokens_blocks(
    app_client,
    auth_headers,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-zero-tokens", tokens=0)
    payload = build_generate_payload(seed_beats)
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers("u-zero-tokens"),
        json=payload,
    )
    assert r.status_code == 402
    body = r.json()
    assert body["error"]["code"] == "INSUFFICIENT_TOKENS"
    assert body["error"]["details"]["required"] == 1
    assert body["error"]["details"]["available"] == 0


@pytest.mark.asyncio
async def test_generate_golden_path_reserves_token(
    app_client,
    auth_headers,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-gold", tokens=5)
    payload = build_generate_payload(seed_beats)
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers("u-gold"),
        json=payload,
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "processing"
    assert body["tokensReserved"] == 1
    assert "jobId" in body

    # Баланс: 4 available + 1 reserved
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("u-gold")
    )
    assert bal.json() == {"available": 4, "reserved": 1, "frozen": False}


@pytest.mark.asyncio
async def test_generate_invalid_tempo_returns_400(
    app_client,
    auth_headers,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-bad-tempo", tokens=5)
    payload = build_generate_payload(seed_beats)
    payload["equalizer"]["tempo"] = 999
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers("u-bad-tempo"),
        json=payload,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_generate_auxiliary_must_be_exactly_three(
    app_client,
    auth_headers,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-bad-aux", tokens=5)
    payload = build_generate_payload(seed_beats)
    payload["instruments"]["drums"]["auxiliary"] = payload["instruments"][
        "drums"
    ]["auxiliary"][:2]
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers("u-bad-aux"),
        json=payload,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_generate_idempotency_key_returns_same_job(
    app_client,
    auth_headers,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-idem", tokens=5)
    payload = build_generate_payload(seed_beats)
    h = auth_headers("u-idem")
    h["Idempotency-Key"] = "client-abc-123"
    r1 = await app_client.post(
        "/v1/tracks/generate", headers=h, json=payload
    )
    assert r1.status_code == 200
    r2 = await app_client.post(
        "/v1/tracks/generate", headers=h, json=payload
    )
    assert r2.status_code == 200
    assert r1.json()["jobId"] == r2.json()["jobId"]
    # Только один резерв
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("u-idem")
    )
    assert bal.json()["reserved"] == 1


@pytest.mark.asyncio
async def test_generate_unknown_beat_returns_404(
    app_client,
    auth_headers,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-bad-beat", tokens=5)
    payload = build_generate_payload("00000000-0000-0000-0000-000000000000")
    r = await app_client.post(
        "/v1/tracks/generate",
        headers=auth_headers("u-bad-beat"),
        json=payload,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "BEAT_NOT_FOUND"
