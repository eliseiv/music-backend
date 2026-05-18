#!/usr/bin/env python3
"""Smoke-тест music-backend: вызывает все /v1/ эндпоинты на живом сервере.

Usage:
    BASE_URL=http://localhost:8000 \\
    API_KEY=xxx \\
    ADAPTY_SECRET=yyy \\
    RF_SECRET=zzz \\
    python scripts/smoke.py

Exit code 0 — все проверки прошли, иначе печатает первую ошибку.
Зависимости: только stdlib (urllib, hmac, hashlib, json, time).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --- config ---

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
X_USER_ID = os.environ.get("X_USER_ID", f"smoke-user-{int(time.time())}")
ADAPTY_SECRET = os.environ.get("ADAPTY_SECRET", "")
RF_SECRET = os.environ.get("RF_SECRET", "")

if not API_KEY:
    print("ERROR: API_KEY env var is required", file=sys.stderr)
    sys.exit(2)

# --- colors ---

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
NC = "\033[0m"
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    GREEN = RED = YELLOW = NC = ""

# --- counters ---

passed: list[str] = []
failed: list[str] = []


def _request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, dict[str, str]]:
    url = f"{BASE_URL}{path}"
    req = Request(url, method=method, data=body)
    if body is not None:
        req.add_header("Content-Type", content_type)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except URLError as e:
        return 0, f"URLError: {e}".encode(), {}


def _auth(extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {API_KEY}", "X-User-Id": X_USER_ID}
    if extra:
        h.update(extra)
    return h


def check(name: str, expected: int, actual: int, body: bytes = b"") -> None:
    if actual == expected:
        print(f"{GREEN}✓{NC} {name} (HTTP {actual})")
        passed.append(name)
    else:
        snippet = body[:200].decode("utf-8", errors="replace")
        print(f"{RED}✗{NC} {name} (expected {expected}, got {actual})")
        print(f"    body: {snippet}")
        failed.append(f"{name}: expected={expected}, got={actual}")


def soft(name: str, ok: bool, msg: str = "") -> None:
    if ok:
        print(f"{GREEN}✓{NC} {name} {msg}")
        passed.append(name)
    else:
        print(f"{RED}✗{NC} {name} {msg}")
        failed.append(name)


def jload(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def build_payload(beat_id: str, *, voice_url: str | None = None) -> dict[str, Any]:
    return {
        "beatId": beat_id,
        "instruments": {
            "harmonic": {
                "bass": {"sampleUrl": "https://placeholder.example/b.wav"},
                "lead": {"sampleUrl": "https://placeholder.example/l.wav"},
                "chord": {"sampleUrl": "https://placeholder.example/c.wav"},
            },
            "drums": {
                "kick": {"sampleUrl": "https://placeholder.example/k.wav"},
                "snare": {"sampleUrl": "https://placeholder.example/s.wav"},
                "openHihat": {"sampleUrl": "https://placeholder.example/o.wav"},
                "closedHihat": {"sampleUrl": "https://placeholder.example/ch.wav"},
                "auxiliary": [
                    {"sampleUrl": "https://placeholder.example/a.wav"},
                    {"sampleUrl": "https://placeholder.example/a.wav"},
                    {"sampleUrl": "https://placeholder.example/a.wav"},
                ],
            },
            "mixing": {"sampleUrl": "https://placeholder.example/m.wav"},
            "soundEffects": {"sampleUrl": "https://placeholder.example/sfx.wav"},
        },
        "equalizer": {
            "tempo": 124,
            "leadDensity": 7,
            "bassDensity": 8,
            "chordDensity": 5,
            "drumDensity": 9,
        },
        "lyricsPrompt": None,
        "voiceUrl": voice_url,
        "production": None,
        "pitch": None,
        "storeStems": False,
        "language": "en",
        "desiredDurationSeconds": 60,
    }


# =========================================================================
print(f"{YELLOW}=== Music Backend smoke test ==={NC}")
print(f"Base URL:    {BASE_URL}")
print(f"X-User-Id:   {X_USER_ID}")
print()

# --- 1. Health (без auth) ---
st, body, _ = _request("GET", "/healthz")
check("GET /healthz", 200, st, body)

# --- 2. Auth: 401 без bearer ---
st, body, _ = _request("GET", "/v1/beats")
check("GET /v1/beats без Bearer → 401", 401, st, body)

# --- 3. Auth: 401 с неверным bearer ---
st, body, _ = _request("GET", "/v1/beats", headers={"Authorization": "Bearer WRONG"})
check("GET /v1/beats wrong Bearer → 401", 401, st, body)

# --- 4. 400 без X-User-Id ---
st, body, _ = _request("GET", "/v1/beats", headers={"Authorization": f"Bearer {API_KEY}"})
check("GET /v1/beats без X-User-Id → 400", 400, st, body)

# --- 5. Catalog: beats ---
st, body, _ = _request("GET", "/v1/beats", headers=_auth())
check("GET /v1/beats → 200", 200, st, body)
beats_data = jload(body) or {}
beats = beats_data.get("beats", [])
beat_id = beats[0]["id"] if beats else None
soft("/v1/beats содержит ≥1 битов", len(beats) >= 1, f"(len={len(beats)})")

# --- 6. Catalog: samples (10 категорий) ---
st, body, _ = _request("GET", "/v1/samples", headers=_auth())
check("GET /v1/samples → 200", 200, st, body)
samples_data = jload(body) or {}
cats = samples_data.get("categories", {})
soft("/v1/samples содержит 10 категорий", len(cats) == 10, f"(len={len(cats)})")

# --- 7. Tokens balance + products ---
st, body, _ = _request("GET", "/v1/tokens/balance", headers=_auth())
check("GET /v1/tokens/balance → 200", 200, st, body)

st, body, _ = _request("GET", "/v1/tokens/products", headers=_auth())
check("GET /v1/tokens/products → 200", 200, st, body)

# --- 8. Generate без подписки → 402 SUBSCRIPTION_REQUIRED ---
if beat_id:
    payload = json.dumps(build_payload(beat_id)).encode()
    st, body, _ = _request("POST", "/v1/tracks/generate", headers=_auth(), body=payload)
    check("POST /v1/tracks/generate без подписки → 402", 402, st, body)
    err = jload(body) or {}
    code = (err.get("error") or {}).get("code")
    soft(
        "  → error.code = SUBSCRIPTION_REQUIRED",
        code == "SUBSCRIPTION_REQUIRED",
        f"(got {code})",
    )

# --- 9. Validation: invalid tempo → 400 INVALID_INPUT ---
if beat_id:
    bad = build_payload(beat_id)
    bad["equalizer"]["tempo"] = 9999
    st, body, _ = _request(
        "POST", "/v1/tracks/generate", headers=_auth(), body=json.dumps(bad).encode()
    )
    check("POST /v1/tracks/generate tempo=9999 → 400", 400, st, body)
    err = jload(body) or {}
    code = (err.get("error") or {}).get("code")
    soft("  → error.code = INVALID_INPUT", code == "INVALID_INPUT", f"(got {code})")

# --- 10. Adapty webhook ---
if ADAPTY_SECRET:
    print()
    print(f"{YELLOW}--- Adapty webhook ---{NC}")
    # Test ping (пустой body) → 200
    st, body, _ = _request(
        "POST",
        "/v1/webhooks/billing/adapty",
        headers={"Authorization": ADAPTY_SECRET},
        body=b"",
    )
    check("POST /v1/webhooks/billing/adapty test-ping (empty body) → 200", 200, st, body)
    resp = jload(body) or {}
    soft(
        "  → status = test_ping",
        resp.get("status") == "test_ping",
        f"(got {resp.get('status')})",
    )

    # Wrong secret → 401
    st, body, _ = _request(
        "POST",
        "/v1/webhooks/billing/adapty",
        headers={"Authorization": "wrong-secret"},
        body=b'{"event_type":"x"}',
    )
    check("POST /v1/webhooks/billing/adapty wrong auth → 401", 401, st, body)

    # Real subscription_started event
    event_id = f"smoke-evt-{uuid.uuid4().hex[:8]}"
    now = datetime.now(tz=timezone.utc)
    body_obj = {
        "event_type": "subscription_started",
        "event_id": event_id,
        "profile_id": X_USER_ID,
        "vendor_product_id": "premium_monthly",
        "event_datetime": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        "token_amount": 10,
    }
    st, body, _ = _request(
        "POST",
        "/v1/webhooks/billing/adapty",
        headers={"Authorization": ADAPTY_SECRET},
        body=json.dumps(body_obj).encode(),
    )
    check("POST /v1/webhooks/billing/adapty subscription_started → 200", 200, st, body)
    resp = jload(body) or {}
    soft(
        "  → status = applied",
        resp.get("status") == "applied",
        f"(got {resp.get('status')})",
    )

    # Дубль event_id → duplicate
    st, body, _ = _request(
        "POST",
        "/v1/webhooks/billing/adapty",
        headers={"Authorization": ADAPTY_SECRET},
        body=json.dumps(body_obj).encode(),
    )
    check("POST /v1/webhooks/billing/adapty dup → 200", 200, st, body)
    resp = jload(body) or {}
    soft(
        "  → status = duplicate",
        resp.get("status") == "duplicate",
        f"(got {resp.get('status')})",
    )

    # Balance ≥10
    st, body, _ = _request("GET", "/v1/tokens/balance", headers=_auth())
    bal = jload(body) or {}
    soft(
        "  → balance.available >= 10",
        bal.get("available", 0) >= 10,
        f"(available={bal.get('available')})",
    )

    # --- 11. Generate теперь должен работать → 200 ---
    if beat_id:
        payload = json.dumps(build_payload(beat_id)).encode()
        st, body, _ = _request(
            "POST", "/v1/tracks/generate", headers=_auth(), body=payload
        )
        check("POST /v1/tracks/generate с подпиской → 200", 200, st, body)
        resp = jload(body) or {}
        job_id = resp.get("jobId")
        soft("  → есть jobId", bool(job_id), f"(jobId={job_id})")

        if job_id:
            # GET job status
            st, body, _ = _request(
                "GET", f"/v1/tracks/jobs/{job_id}", headers=_auth()
            )
            check("GET /v1/tracks/jobs/{id} → 200", 200, st, body)
            j = jload(body) or {}
            soft(
                "  → status в [queued,processing,succeeded,failed]",
                j.get("status") in {"queued", "processing", "succeeded", "failed"},
                f"(status={j.get('status')})",
            )
            soft(
                "  → pipeline есть в ответе",
                isinstance(j.get("pipeline"), list),
                f"(pipeline.length={len(j.get('pipeline') or [])})",
            )

            # --- 12. Idempotency-Key — дубль возвращает тот же jobId ---
            idem = f"smoke-idem-{uuid.uuid4().hex[:8]}"
            st1, body1, _ = _request(
                "POST",
                "/v1/tracks/generate",
                headers=_auth({"Idempotency-Key": idem}),
                body=payload,
            )
            st2, body2, _ = _request(
                "POST",
                "/v1/tracks/generate",
                headers=_auth({"Idempotency-Key": idem}),
                body=payload,
            )
            check("Idempotency 1st → 200", 200, st1, body1)
            check("Idempotency 2nd → 200", 200, st2, body2)
            j1 = jload(body1) or {}
            j2 = jload(body2) or {}
            soft(
                "  → одинаковый jobId",
                j1.get("jobId") == j2.get("jobId"),
                f"({j1.get('jobId')} == {j2.get('jobId')})",
            )

# --- 13. RuStore webhook ---
if RF_SECRET:
    print()
    print(f"{YELLOW}--- RuStore webhook ---{NC}")
    rf_body = json.dumps(
        {
            "event_type": "SUBSCRIPTION_PURCHASED",
            "event_id": f"smoke-rf-{uuid.uuid4().hex[:8]}",
            "user_id": f"rf-smoke-{int(time.time())}",
            "product_id": "premium_monthly",
            "token_amount": 5,
            "occurred_at": "2026-05-18T10:00:00Z",
            "expires_at": "2026-06-18T10:00:00Z",
        }
    ).encode()
    sig = hmac.new(RF_SECRET.encode(), rf_body, hashlib.sha256).hexdigest()
    st, body, _ = _request(
        "POST",
        "/v1/webhooks/billing/rf",
        headers={"X-RuStore-Signature": sig},
        body=rf_body,
    )
    check("POST /v1/webhooks/billing/rf valid sig → 200", 200, st, body)

    # test ping (empty body) → 200 test_ping
    st, body, _ = _request(
        "POST", "/v1/webhooks/billing/rf", body=b""
    )
    check("POST /v1/webhooks/billing/rf empty body → 200", 200, st, body)

    # Wrong signature → 401
    st, body, _ = _request(
        "POST",
        "/v1/webhooks/billing/rf",
        headers={"X-RuStore-Signature": "wrong"},
        body=rf_body,
    )
    check("POST /v1/webhooks/billing/rf wrong sig → 401", 401, st, body)

# --- 14. fal webhook without signature → 401 ---
st, body, _ = _request(
    "POST",
    "/v1/webhooks/fal",
    body=b'{"request_id":"x","status":"completed"}',
)
check("POST /v1/webhooks/fal без подписи → 401", 401, st, body)

# --- 15. Voice upload: invalid content-type → 400 ---
st, body, headers = _request(
    "POST",
    "/v1/uploads/voice",
    headers=_auth(),
    body=(
        b"--BOUNDARY\r\n"
        b'Content-Disposition: form-data; name="file"; filename="x.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
        b"hello\r\n"
        b"--BOUNDARY--\r\n"
    ),
    content_type="multipart/form-data; boundary=BOUNDARY",
)
check("POST /v1/uploads/voice wrong content-type → 400", 400, st, body)

# --- 16. Swagger / OpenAPI ---
st, body, _ = _request("GET", "/openapi.json")
check("GET /openapi.json → 200", 200, st, body)

st, body, _ = _request("GET", "/docs")
check("GET /docs → 200", 200, st, body)

# =========================================================================
print()
print(f"{YELLOW}=== Summary ==={NC}")
print(f"Passed: {GREEN}{len(passed)}{NC}")
print(f"Failed: {RED}{len(failed)}{NC}")
if failed:
    print()
    print("Failed:")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
