from __future__ import annotations

import hashlib
import hmac
import json

import pytest


# --- helpers ---


def _adapty_headers(secret: str = "test-adapty-secret") -> dict[str, str]:
    return {
        "Authorization": secret,
        "Content-Type": "application/json",
    }


def _rf_headers(body: bytes, secret: str = "test-rf-secret") -> dict[str, str]:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-RuStore-Signature": sig,
        "Content-Type": "application/json",
    }


def _adapty_event(
    *,
    event_type: str,
    profile_id: str,
    event_id: str,
    product: str = "premium_monthly",
    expires_in_days: int | None = 30,
    token_amount: int | None = None,
) -> dict:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(tz=timezone.utc)
    payload = {
        "event_type": event_type,
        "event_id": event_id,
        "profile_id": profile_id,
        "vendor_product_id": product,
        "event_datetime": now.isoformat().replace("+00:00", "Z"),
    }
    if expires_in_days is not None:
        payload["expires_at"] = (
            (now + timedelta(days=expires_in_days))
            .isoformat()
            .replace("+00:00", "Z")
        )
    if token_amount is not None:
        payload["token_amount"] = token_amount
    return payload


# --- Adapty ---


@pytest.mark.asyncio
async def test_adapty_subscription_purchased_activates(
    app_client, auth_headers
):
    body = _adapty_event(
        event_type="subscription_started",
        profile_id="ada-1",
        event_id="ada-evt-1",
        token_amount=100,
    )
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(body).encode(),
        headers=_adapty_headers(),
    )
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "applied"

    # Баланс пополнился, кошелёк не frozen.
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("ada-1")
    )
    assert bal.json() == {"available": 100, "reserved": 0, "frozen": False}


@pytest.mark.asyncio
async def test_adapty_duplicate_event_returns_duplicate(app_client):
    body = _adapty_event(
        event_type="subscription_started",
        profile_id="ada-2",
        event_id="dup-1",
        token_amount=10,
    )
    raw = json.dumps(body).encode()
    r1 = await app_client.post(
        "/v1/webhooks/billing/adapty", content=raw, headers=_adapty_headers()
    )
    r2 = await app_client.post(
        "/v1/webhooks/billing/adapty", content=raw, headers=_adapty_headers()
    )
    assert r1.json()["status"] == "applied"
    assert r2.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_adapty_subscription_expired_freezes_wallet(
    app_client, auth_headers
):
    # 1) Сначала активируем
    activate = _adapty_event(
        event_type="subscription_started",
        profile_id="ada-exp",
        event_id="evt-act",
        token_amount=5,
    )
    await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(activate).encode(),
        headers=_adapty_headers(),
    )
    # 2) Истекла
    expire = _adapty_event(
        event_type="subscription_expired",
        profile_id="ada-exp",
        event_id="evt-exp",
        expires_in_days=None,
    )
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(expire).encode(),
        headers=_adapty_headers(),
    )
    assert r.json()["status"] == "applied"
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("ada-exp")
    )
    body = bal.json()
    assert body["frozen"] is True
    assert body["available"] == 5  # токены сохранены, но frozen


@pytest.mark.asyncio
async def test_adapty_one_time_purchase_credits_via_product_lookup(
    app_client, auth_headers, seed_token_products
):
    body = _adapty_event(
        event_type="non_subscription_purchase",
        profile_id="ada-otp",
        event_id="evt-otp",
        product="com.test.tokens_10",
    )
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(body).encode(),
        headers=_adapty_headers(),
    )
    assert r.json()["status"] == "applied"
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("ada-otp")
    )
    assert bal.json()["available"] == 10  # из token_products


@pytest.mark.asyncio
async def test_adapty_refund_clamps_at_zero(app_client, auth_headers):
    # Активируем + 5 токенов
    await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(
            _adapty_event(
                event_type="subscription_started",
                profile_id="ada-ref",
                event_id="ref-act",
                token_amount=5,
            )
        ).encode(),
        headers=_adapty_headers(),
    )
    # Refund 10 (больше, чем есть)
    body = _adapty_event(
        event_type="refund",
        profile_id="ada-ref",
        event_id="ref-rfd",
        token_amount=10,
    )
    body["token_amount"] = 10
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(body).encode(),
        headers=_adapty_headers(),
    )
    assert r.json()["status"] == "applied"
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("ada-ref")
    )
    assert bal.json()["available"] == 0  # clamped


@pytest.mark.asyncio
async def test_adapty_invalid_auth_returns_401(app_client):
    body = _adapty_event(
        event_type="subscription_started",
        profile_id="ada-x",
        event_id="x",
        token_amount=1,
    )
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(body).encode(),
        headers={
            "Authorization": "wrong-secret",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_adapty_real_access_level_updated_activates(app_client, auth_headers):
    """Реальный формат Adapty: поля во вложенном event_properties,
    event_id = profile_event_id, активность по is_active. Это то, что Adapty
    реально шлёт при покупке/промокоде/trial (раньше дропалось как
    unknown_event_type → 'Active subscription required')."""
    profile = "5340429e-real-fmt"
    body = {
        "profile_id": profile,
        "customer_user_id": None,
        "event_type": "access_level_updated",
        "event_datetime": "2026-06-15T10:56:45.630622+0000",
        "event_properties": {
            "profile_event_id": "evt-real-1",
            "profile_id": profile,
            "vendor_product_id": "week_6.99_nottrial",
            "transaction_id": "230003474116029",
            "subscription_expires_at": "2026-06-22T10:56:31.000000+0000",
            "access_level_id": "premium",
            "is_active": True,
            "expires_at": "2026-06-22T10:56:31.000000+0000",
            "profile_has_access_level": True,
        },
        "event_api_version": 1,
    }
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=json.dumps(body).encode(),
        headers=_adapty_headers(),
    )
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "applied"

    # Подписка активна → кошелёк не frozen (генерация не блокируется gate'ом)
    bal = await app_client.get("/v1/tokens/balance", headers=auth_headers(profile))
    assert bal.json()["frozen"] is False


@pytest.mark.asyncio
async def test_adapty_access_level_inactive_expires(app_client, auth_headers):
    """access_level_updated с is_active=false → подписка истекает (frozen)."""
    profile = "exp-fmt-1"
    # сначала активируем
    active = {
        "profile_id": profile, "event_type": "access_level_updated",
        "event_datetime": "2026-06-15T10:00:00+0000",
        "event_properties": {
            "profile_event_id": "exp-evt-active", "is_active": True,
            "expires_at": "2026-07-15T10:00:00+0000",
        },
    }
    await app_client.post("/v1/webhooks/billing/adapty",
        content=json.dumps(active).encode(), headers=_adapty_headers())
    # затем истечение
    expired = {
        "profile_id": profile, "event_type": "access_level_updated",
        "event_datetime": "2026-07-15T10:00:01+0000",
        "event_properties": {
            "profile_event_id": "exp-evt-inactive", "is_active": False,
        },
    }
    r = await app_client.post("/v1/webhooks/billing/adapty",
        content=json.dumps(expired).encode(), headers=_adapty_headers())
    assert r.status_code == 200
    bal = await app_client.get("/v1/tokens/balance", headers=auth_headers(profile))
    assert bal.json()["frozen"] is True


@pytest.mark.asyncio
async def test_adapty_validation_request_returns_2xx(app_client):
    """Adapty при сохранении интеграции шлёт валидационный запрос с
    нестандартным телом. Авторизация валидна → должны вернуть 2XX (не 400),
    иначе Adapty отвергнет endpoint."""
    # Неизвестный event_type — parse_event бросил бы WebhookPayloadInvalid
    raw = json.dumps({"event_type": "some_adapty_validation_probe"}).encode()
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=raw,
        headers=_adapty_headers(),
    )
    assert r.status_code == 200, r.json()
    assert r.json()["status"] in ("ignored", "test_ping")


@pytest.mark.asyncio
async def test_adapty_empty_body_is_test_ping(app_client):
    r = await app_client.post(
        "/v1/webhooks/billing/adapty",
        content=b"",
        headers=_adapty_headers(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "test_ping"


# --- RuStore ---


@pytest.mark.asyncio
async def test_rustore_subscription_purchased_activates(
    app_client, auth_headers
):
    body = {
        "event_type": "SUBSCRIPTION_PURCHASED",
        "event_id": "rf-1",
        "user_id": "rs-1",
        "product_id": "premium_monthly",
        "token_amount": 50,
        "occurred_at": "2026-05-18T10:00:00Z",
        "expires_at": "2026-06-18T10:00:00Z",
    }
    raw = json.dumps(body).encode()
    r = await app_client.post(
        "/v1/webhooks/billing/rf", content=raw, headers=_rf_headers(raw)
    )
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "applied"
    bal = await app_client.get(
        "/v1/tokens/balance", headers=auth_headers("rs-1")
    )
    assert bal.json() == {"available": 50, "reserved": 0, "frozen": False}


@pytest.mark.asyncio
async def test_rustore_invalid_signature_returns_401(app_client):
    body = b'{"event_type":"SUBSCRIPTION_PURCHASED","event_id":"x","user_id":"u"}'
    r = await app_client.post(
        "/v1/webhooks/billing/rf",
        content=body,
        headers={
            "X-RuStore-Signature": "wrong",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
