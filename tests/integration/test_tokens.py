"""Tokens: balance + products."""
from __future__ import annotations


async def test_new_user_balance_is_zero(app_client, auth_headers):
    r = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("fresh-user")
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"available": 0, "reserved": 0, "frozen": False}


async def test_balance_reflects_credit(
    app_client, auth_headers, make_user_with_subscription
):
    await make_user_with_subscription("u-with-tokens", tokens=42)
    r = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("u-with-tokens")
    )
    body = r.json()
    assert body["available"] == 42
    assert body["reserved"] == 0
    assert body["frozen"] is False


async def test_list_token_products(
    app_client, auth_headers, seed_token_products
):
    r = await app_client.get(
        "/v1/tokens/products", headers=auth_headers()
    )
    assert r.status_code == 200
    body = r.json()
    assert "products" in body
    assert len(body["products"]) == 1
    p = body["products"][0]
    assert p["code"] == "tokens_10"
    assert p["tokenAmount"] == 10
