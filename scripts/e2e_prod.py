#!/usr/bin/env python3
"""End-to-end тест продового music-backend с реальной генерацией через fal.

Usage:
    BASE_URL=https://appsprobek.shop \
    API_KEY=... ADAPTY_SECRET=... RF_SECRET=... \
    python scripts/e2e_prod.py

Что делает:
  1. Активирует подписку через Adapty webhook (10 токенов).
  2. Проверяет баланс.
  3. Запускает РЕАЛЬНУЮ генерацию через fal с публично доступными URL.
  4. Polling status до succeeded/failed (макс 4 минуты).
  5. Скачивает audioUrl, проверяет что это валидный mp3/wav (по magic bytes).
  6. Прогоняет негативные сценарии (401/402/403/404/INVALID_SAMPLE_URL).
  7. Проверяет voice upload с реальным WAV.

Зависимости: только stdlib.
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

BASE_URL = os.environ.get("BASE_URL", "https://appsprobek.shop").rstrip("/")
API_KEY = os.environ["API_KEY"]
ADAPTY_SECRET = os.environ.get("ADAPTY_SECRET", "")
RF_SECRET = os.environ.get("RF_SECRET", "")

X_USER_PRIMARY = f"e2e-{uuid.uuid4().hex[:8]}"
X_USER_SECONDARY = f"e2e-other-{uuid.uuid4().hex[:8]}"

# Реальные публично доступные аудио для прохождения URL-валидатора
SAMPLE_URL = "https://download.samplelib.com/wav/sample-3s.wav"
VOICE_URL_REAL = "https://download.samplelib.com/mp3/sample-3s.mp3"

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
NC = "\033[0m"
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    GREEN = RED = YELLOW = NC = ""

passed: list[str] = []
failed: list[str] = []


def req(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
    timeout: int = 30,
) -> tuple[int, bytes, dict[str, str]]:
    url = f"{BASE_URL}{path}"
    r = Request(url, method=method, data=body)
    if body is not None:
        r.add_header("Content-Type", content_type)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except URLError as e:
        return 0, f"URLError: {e}".encode(), {}


def auth(user: str = X_USER_PRIMARY, extra: dict[str, str] | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {API_KEY}", "X-User-Id": user}
    if extra:
        h.update(extra)
    return h


def check(name: str, expected: int, actual: int, body: bytes = b"") -> None:
    if actual == expected:
        print(f"{GREEN}✓{NC} {name} (HTTP {actual})")
        passed.append(name)
    else:
        snippet = body[:300].decode("utf-8", errors="replace")
        print(f"{RED}✗{NC} {name} (expected {expected}, got {actual})")
        print(f"    {snippet}")
        failed.append(f"{name}: expected={expected}, got={actual}")


def soft(name: str, ok: bool, info: str = "") -> None:
    if ok:
        print(f"{GREEN}✓{NC} {name} {info}")
        passed.append(name)
    else:
        print(f"{RED}✗{NC} {name} {info}")
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
                "bass": {"sampleUrl": SAMPLE_URL},
                "lead": {"sampleUrl": SAMPLE_URL},
                "chord": {"sampleUrl": SAMPLE_URL},
            },
            "drums": {
                "kick": {"sampleUrl": SAMPLE_URL},
                "snare": {"sampleUrl": SAMPLE_URL},
                "openHihat": {"sampleUrl": SAMPLE_URL},
                "closedHihat": {"sampleUrl": SAMPLE_URL},
                "auxiliary": [
                    {"sampleUrl": SAMPLE_URL},
                    {"sampleUrl": SAMPLE_URL},
                    {"sampleUrl": SAMPLE_URL},
                ],
            },
            "mixing": {"sampleUrl": SAMPLE_URL},
            "soundEffects": {"sampleUrl": SAMPLE_URL},
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
        "desiredDurationSeconds": 30,
    }


def banner(s: str) -> None:
    print()
    print(f"{YELLOW}=== {s} ==={NC}")


# ================================================================
banner("0. Setup")
print(f"Base URL:        {BASE_URL}")
print(f"X-User (primary):   {X_USER_PRIMARY}")
print(f"X-User (secondary): {X_USER_SECONDARY}")
print(f"Sample URL:      {SAMPLE_URL}")

# ================================================================
banner("1. Авторизация и заголовки")

st, body, _ = req("GET", "/healthz")
check("GET /healthz", 200, st, body)

st, body, _ = req("GET", "/v1/beats")
check("/v1/beats без Authorization → 401", 401, st, body)
err = jload(body) or {}
soft(
    "  → code = UNAUTHORIZED",
    (err.get("error") or {}).get("code") == "UNAUTHORIZED",
    f"(got {(err.get('error') or {}).get('code')})",
)

st, body, _ = req("GET", "/v1/beats", headers={"Authorization": "Bearer WRONG"})
check("/v1/beats wrong Bearer → 401", 401, st, body)

st, body, _ = req("GET", "/v1/beats", headers={"Authorization": f"Bearer {API_KEY}"})
check("/v1/beats без X-User-Id → 400", 400, st, body)
err = jload(body) or {}
soft(
    "  → code = MISSING_X_USER_ID",
    (err.get("error") or {}).get("code") == "MISSING_X_USER_ID",
    f"(got {(err.get('error') or {}).get('code')})",
)

# ================================================================
banner("2. Catalog: beats + samples")

st, body, _ = req("GET", "/v1/beats", headers=auth())
check("/v1/beats → 200", 200, st, body)
beats = (jload(body) or {}).get("beats", [])
soft("  → ≥1 битов в каталоге", len(beats) >= 1, f"(len={len(beats)})")
beat_id = beats[0]["id"] if beats else None
print(f"    beat_id для генерации: {beat_id}")

st, body, _ = req("GET", "/v1/samples", headers=auth())
check("/v1/samples → 200", 200, st, body)
cats = (jload(body) or {}).get("categories", {})
soft("  → 10 категорий", len(cats) == 10, f"(len={len(cats)})")

# ================================================================
banner("3. Negative: validation")

if beat_id:
    # Неверный tempo
    bad = build_payload(beat_id)
    bad["equalizer"]["tempo"] = 9999
    st, body, _ = req(
        "POST", "/v1/tracks/generate", headers=auth(), body=json.dumps(bad).encode()
    )
    check("generate tempo=9999 → 400", 400, st, body)
    err = jload(body) or {}
    soft(
        "  → code = INVALID_INPUT",
        (err.get("error") or {}).get("code") == "INVALID_INPUT",
        f"(got {(err.get('error') or {}).get('code')})",
    )

    # auxiliary != 3
    bad = build_payload(beat_id)
    bad["instruments"]["drums"]["auxiliary"] = bad["instruments"]["drums"]["auxiliary"][:2]
    st, body, _ = req(
        "POST", "/v1/tracks/generate", headers=auth(), body=json.dumps(bad).encode()
    )
    check("generate auxiliary=2 → 400", 400, st, body)

    # Битый sample URL
    bad = build_payload(beat_id)
    bad["instruments"]["mixing"]["sampleUrl"] = "https://this-domain-does-not-exist-xxx.invalid/x.wav"
    st, body, _ = req(
        "POST", "/v1/tracks/generate", headers=auth(), body=json.dumps(bad).encode(), timeout=15
    )
    check("generate битый sample_url → 400", 400, st, body)
    err = jload(body) or {}
    soft(
        "  → code = INVALID_SAMPLE_URL",
        (err.get("error") or {}).get("code") == "INVALID_SAMPLE_URL",
        f"(got {(err.get('error') or {}).get('code')})",
    )

# ================================================================
banner("4. Без подписки → 402 SUBSCRIPTION_REQUIRED")

if beat_id:
    payload = json.dumps(build_payload(beat_id)).encode()
    st, body, _ = req(
        "POST",
        "/v1/tracks/generate",
        headers=auth(),
        body=payload,
        timeout=15,
    )
    check("generate без подписки → 402", 402, st, body)
    err = jload(body) or {}
    soft(
        "  → code = SUBSCRIPTION_REQUIRED",
        (err.get("error") or {}).get("code") == "SUBSCRIPTION_REQUIRED",
        f"(got {(err.get('error') or {}).get('code')})",
    )

# ================================================================
banner("5. Adapty webhook: активируем подписку + 10 токенов")

if not ADAPTY_SECRET:
    print(f"{YELLOW}skip — ADAPTY_SECRET не задан{NC}")
    sys.exit(1)

# Test ping
st, body, _ = req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY_SECRET},
    body=b"",
)
check("adapty test-ping (empty) → 200", 200, st, body)
soft("  → status = test_ping", (jload(body) or {}).get("status") == "test_ping")

# Wrong auth
st, body, _ = req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": "wrong-secret"},
    body=b'{"event_type":"x"}',
)
check("adapty wrong auth → 401", 401, st, body)

# subscription_started для primary user
event_id = f"e2e-sub-{uuid.uuid4().hex[:8]}"
now = datetime.now(tz=timezone.utc)
sub_body = {
    "event_type": "subscription_started",
    "event_id": event_id,
    "profile_id": X_USER_PRIMARY,
    "vendor_product_id": "premium_monthly",
    "event_datetime": now.isoformat().replace("+00:00", "Z"),
    "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
    "token_amount": 10,
}
st, body, _ = req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY_SECRET},
    body=json.dumps(sub_body).encode(),
)
check("adapty subscription_started → 200", 200, st, body)
soft("  → status = applied", (jload(body) or {}).get("status") == "applied")

# Duplicate
st, body, _ = req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY_SECRET},
    body=json.dumps(sub_body).encode(),
)
check("adapty duplicate event → 200", 200, st, body)
soft("  → status = duplicate", (jload(body) or {}).get("status") == "duplicate")

# Balance
st, body, _ = req("GET", "/v1/tokens/balance", headers=auth())
bal = jload(body) or {}
soft(
    f"balance.available == 10",
    bal.get("available") == 10,
    f"(got {bal})",
)

# ================================================================
banner("6. Voice upload (опц.)")

# Скачаем реальный wav и загрузим
try:
    with urlopen(VOICE_URL_REAL, timeout=15) as r:
        voice_bytes = r.read()
    print(f"   downloaded reference voice: {len(voice_bytes)} bytes")
    boundary = "----E2E" + uuid.uuid4().hex
    multipart = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="voice.mp3"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode() + voice_bytes + f"\r\n--{boundary}--\r\n".encode()
    st, body, _ = req(
        "POST",
        "/v1/uploads/voice",
        headers=auth(),
        body=multipart,
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=60,
    )
    check("upload voice → 200", 200, st, body)
    voice_resp = jload(body) or {}
    uploaded_voice_url = voice_resp.get("voiceUrl")
    soft("  → есть voiceUrl", bool(uploaded_voice_url), f"({uploaded_voice_url})")
except Exception as e:
    soft("upload voice", False, f"(exception {e})")
    uploaded_voice_url = None

# ================================================================
banner("7. РЕАЛЬНАЯ генерация трека через fal")

if beat_id is None:
    print(f"{RED}beat_id отсутствует — пропускаем{NC}")
    sys.exit(1)

payload = json.dumps(build_payload(beat_id)).encode()
print(f"   POST /v1/tracks/generate (sample URLs = {SAMPLE_URL})")
st, body, _ = req(
    "POST",
    "/v1/tracks/generate",
    headers=auth(),
    body=payload,
    timeout=30,
)
check("generate с подпиской → 200", 200, st, body)
resp = jload(body) or {}
job_id = resp.get("jobId")
soft(
    "  → есть jobId",
    bool(job_id),
    f"(jobId={job_id}, tokensReserved={resp.get('tokensReserved')})",
)
if not job_id:
    print(f"    body: {body[:500].decode('utf-8', errors='replace')}")

# Polling status
if job_id:
    print(f"   Polling jobs/{job_id} (max 4 минуты, реальный fal)...")
    final_status = None
    final_body = None
    started = time.time()
    deadline = started + 240  # 4 минуты
    while time.time() < deadline:
        st, body, _ = req(
            "GET", f"/v1/tracks/jobs/{job_id}", headers=auth(), timeout=15
        )
        if st == 200:
            j = jload(body) or {}
            status = j.get("status")
            stage = j.get("stage")
            elapsed = int(time.time() - started)
            print(f"   [{elapsed:>3}s] status={status:<11} stage={stage}")
            if status in ("succeeded", "failed", "canceled"):
                final_status = status
                final_body = j
                break
        time.sleep(5)

    soft(
        "  → status = succeeded",
        final_status == "succeeded",
        f"(final={final_status})",
    )

    if final_status == "succeeded":
        # Pipeline stages
        pipeline = final_body.get("pipeline") or []
        stages = {e["stage"]: e["status"] for e in pipeline}
        soft(
            f"  → pipeline содержит 8 стадий ({len(stages)})",
            len(stages) == 8,
            f"(стадии: {list(stages.keys())})",
        )

        # Track endpoint
        track_id = final_body.get("trackId")
        soft("  → есть trackId", bool(track_id), f"({track_id})")
        if track_id:
            st, body, _ = req(
                "GET", f"/v1/tracks/{track_id}", headers=auth(), timeout=15
            )
            check(f"GET /v1/tracks/{{trackId}} → 200", 200, st, body)
            track = jload(body) or {}
            audio_url = track.get("audioUrl")
            soft("  → есть audioUrl", bool(audio_url), f"({audio_url})")
            soft(
                "  → durationSeconds > 0",
                (track.get("durationSeconds") or 0) > 0,
                f"({track.get('durationSeconds')})",
            )

            # Скачать аудио и проверить
            if audio_url:
                try:
                    with urlopen(audio_url, timeout=60) as r:
                        audio_bytes = r.read()
                    soft(
                        f"  → audio файл скачан",
                        len(audio_bytes) > 1000,
                        f"({len(audio_bytes)} bytes)",
                    )
                    magic = audio_bytes[:12]
                    is_wav = magic.startswith(b"RIFF") and b"WAVE" in magic
                    is_mp3 = magic[:3] == b"ID3" or magic[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
                    is_mp4 = b"ftyp" in magic
                    is_ogg = magic.startswith(b"OggS")
                    soft(
                        f"  → audio формат валидный",
                        is_wav or is_mp3 or is_mp4 or is_ogg,
                        f"(magic={magic.hex()})",
                    )
                except Exception as e:
                    soft("  → скачать audio", False, f"(exception {e})")
    elif final_status == "failed":
        print(f"   final body: {json.dumps(final_body, indent=2, ensure_ascii=False)[:600]}")
    else:
        print(f"   TIMEOUT после 4 минут, последний статус: {final_status}")

# ================================================================
banner("8. Idempotency-Key")

if beat_id:
    idem = f"e2e-idem-{uuid.uuid4().hex[:8]}"
    payload = json.dumps(build_payload(beat_id)).encode()
    st1, body1, _ = req(
        "POST",
        "/v1/tracks/generate",
        headers=auth(extra={"Idempotency-Key": idem}),
        body=payload,
        timeout=30,
    )
    st2, body2, _ = req(
        "POST",
        "/v1/tracks/generate",
        headers=auth(extra={"Idempotency-Key": idem}),
        body=payload,
        timeout=30,
    )
    check("Idempotency-Key 1st → 200", 200, st1, body1)
    check("Idempotency-Key 2nd → 200", 200, st2, body2)
    j1 = jload(body1) or {}
    j2 = jload(body2) or {}
    soft(
        "  → одинаковый jobId",
        j1.get("jobId") == j2.get("jobId"),
        f"({j1.get('jobId')} == {j2.get('jobId')})",
    )

# ================================================================
banner("9. Cross-user FORBIDDEN")

# Активируем secondary user (отдельная подписка, чтобы не делить с primary)
sec_event_id = f"e2e-sec-{uuid.uuid4().hex[:8]}"
sec_sub = {
    "event_type": "subscription_started",
    "event_id": sec_event_id,
    "profile_id": X_USER_SECONDARY,
    "vendor_product_id": "premium_monthly",
    "event_datetime": now.isoformat().replace("+00:00", "Z"),
    "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
    "token_amount": 5,
}
req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY_SECRET},
    body=json.dumps(sec_sub).encode(),
)

# Берём ранее созданный job_id от primary и пытаемся прочитать от secondary
if job_id:
    st, body, _ = req(
        "GET", f"/v1/tracks/jobs/{job_id}", headers=auth(user=X_USER_SECONDARY)
    )
    check("чужой job → 403 (для другого X-User-Id)", 403, st, body)
    err = jload(body) or {}
    soft(
        "  → code = FORBIDDEN",
        (err.get("error") or {}).get("code") == "FORBIDDEN",
        f"(got {(err.get('error') or {}).get('code')})",
    )

# ================================================================
banner("10. JOB_NOT_FOUND / TRACK_NOT_FOUND")

fake_id = "00000000-0000-0000-0000-000000000000"
st, body, _ = req("GET", f"/v1/tracks/jobs/{fake_id}", headers=auth())
check("несуществующий job → 404", 404, st, body)
err = jload(body) or {}
soft(
    "  → code = JOB_NOT_FOUND",
    (err.get("error") or {}).get("code") == "JOB_NOT_FOUND",
    f"(got {(err.get('error') or {}).get('code')})",
)
st, body, _ = req("GET", f"/v1/tracks/{fake_id}", headers=auth())
check("несуществующий track → 404", 404, st, body)
err = jload(body) or {}
soft(
    "  → code = TRACK_NOT_FOUND",
    (err.get("error") or {}).get("code") == "TRACK_NOT_FOUND",
    f"(got {(err.get('error') or {}).get('code')})",
)

# ================================================================
banner("11. RuStore webhook (опц.)")

if RF_SECRET:
    rf_body = json.dumps(
        {
            "event_type": "SUBSCRIPTION_PURCHASED",
            "event_id": f"e2e-rf-{uuid.uuid4().hex[:8]}",
            "user_id": f"e2e-rf-user-{int(time.time())}",
            "product_id": "premium_monthly",
            "token_amount": 5,
            "occurred_at": "2026-05-18T10:00:00Z",
            "expires_at": "2026-06-18T10:00:00Z",
        }
    ).encode()
    sig = hmac.new(RF_SECRET.encode(), rf_body, hashlib.sha256).hexdigest()
    st, body, _ = req(
        "POST",
        "/v1/webhooks/billing/rf",
        headers={"X-RuStore-Signature": sig},
        body=rf_body,
    )
    check("rustore valid sig → 200", 200, st, body)

    st, body, _ = req(
        "POST",
        "/v1/webhooks/billing/rf",
        headers={"X-RuStore-Signature": "wrong"},
        body=rf_body,
    )
    check("rustore wrong sig → 401", 401, st, body)

# ================================================================
banner("12. fal webhook без подписи")

st, body, _ = req(
    "POST",
    "/v1/webhooks/fal",
    body=b'{"request_id":"x","status":"completed"}',
)
check("fal без подписи → 401", 401, st, body)

# ================================================================
banner("13. SUBSCRIPTION_EXPIRED freezes wallet")

# Активируем третьего user, потом отправляем subscription_expired
X_USER_EXP = f"e2e-exp-{uuid.uuid4().hex[:8]}"

# 1) Активация
req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY_SECRET},
    body=json.dumps(
        {
            "event_type": "subscription_started",
            "event_id": f"e2e-exp-act-{uuid.uuid4().hex[:8]}",
            "profile_id": X_USER_EXP,
            "vendor_product_id": "premium_monthly",
            "event_datetime": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            "token_amount": 7,
        }
    ).encode(),
)

# 2) subscription_expired
later = now + timedelta(seconds=10)
req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY_SECRET},
    body=json.dumps(
        {
            "event_type": "subscription_expired",
            "event_id": f"e2e-exp-evt-{uuid.uuid4().hex[:8]}",
            "profile_id": X_USER_EXP,
            "vendor_product_id": "premium_monthly",
            "event_datetime": later.isoformat().replace("+00:00", "Z"),
        }
    ).encode(),
)

# 3) Баланс: токены сохранились, frozen=true
st, body, _ = req("GET", "/v1/tokens/balance", headers=auth(user=X_USER_EXP))
bal = jload(body) or {}
soft(
    f"expired user: frozen=true",
    bal.get("frozen") is True,
    f"(balance={bal})",
)
soft(
    f"expired user: токены сохранились (available=7)",
    bal.get("available") == 7,
)

# 4) Попытка генерации после expiration → 402 SUBSCRIPTION_EXPIRED
if beat_id:
    st, body, _ = req(
        "POST",
        "/v1/tracks/generate",
        headers=auth(user=X_USER_EXP),
        body=json.dumps(build_payload(beat_id)).encode(),
        timeout=15,
    )
    check("generate после expiration → 402", 402, st, body)
    err = jload(body) or {}
    soft(
        "  → code = SUBSCRIPTION_EXPIRED",
        (err.get("error") or {}).get("code") == "SUBSCRIPTION_EXPIRED",
        f"(got {(err.get('error') or {}).get('code')})",
    )

# ================================================================
banner("14. Swagger / OpenAPI / X-Request-Id")

st, body, _ = req("GET", "/openapi.json")
check("openapi.json → 200", 200, st, body)
spec = jload(body) or {}
tags = [t["name"] for t in spec.get("tags", [])]
soft(
    f"  → есть русские теги",
    "Генерация треков" in tags and "Каталог" in tags,
    f"(tags={tags})",
)

st, body, headers = req(
    "GET",
    "/v1/tokens/balance",
    headers=auth(extra={"X-Request-Id": "e2e-rid-test-123"}),
)
soft(
    "X-Request-Id проброшен в ответе",
    headers.get("X-Request-Id") == "e2e-rid-test-123" or headers.get("x-request-id") == "e2e-rid-test-123",
    f"(got {headers.get('X-Request-Id') or headers.get('x-request-id')})",
)

# ================================================================
print()
print(f"{YELLOW}=" * 60 + NC)
print(f"Passed: {GREEN}{len(passed)}{NC}")
print(f"Failed: {RED}{len(failed)}{NC}")
if failed:
    print()
    print("FAILED:")
    for f in failed:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
