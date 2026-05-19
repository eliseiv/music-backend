"""One-shot final generation test against prod with local /static/ URLs."""
import json, time, urllib.request, uuid
from datetime import datetime, timedelta, timezone

BASE = "https://appsprobek.shop"
KEY = "mDnLY5ucar8yfC6YMVkg5ECcbvi3lAhB"
ADAPTY = "ghtbPDIBrbTw_0wcQku6nxsWEZ4hOBztg3Y9UdAvjG0"
USER = f"local-final-{uuid.uuid4().hex[:6]}"


def req(method, path, headers=None, body=None):
    r = urllib.request.Request(BASE + path, method=method, data=body)
    if body:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# 1. Подписка
now = datetime.now(tz=timezone.utc)
sub = {
    "event_type": "subscription_started",
    "event_id": f"final-{uuid.uuid4().hex[:8]}",
    "profile_id": USER,
    "vendor_product_id": "premium_monthly",
    "event_datetime": now.isoformat().replace("+00:00", "Z"),
    "expires_at": (now + timedelta(days=30)).isoformat().replace("+00:00", "Z"),
    "token_amount": 10,
}
st, b = req(
    "POST",
    "/v1/webhooks/billing/adapty",
    headers={"Authorization": ADAPTY},
    body=json.dumps(sub).encode(),
)
print(f"sub activate: HTTP {st}")

# 2. Beat
st, b = req(
    "GET",
    "/v1/beats",
    headers={"Authorization": f"Bearer {KEY}", "X-User-Id": USER},
)
beats = json.loads(b)["beats"]
beat = beats[0]
print(f"beat: {beat['genre']:22s} {beat['audioUrl']}")

# 3. Samples
st, b = req(
    "GET",
    "/v1/samples",
    headers={"Authorization": f"Bearer {KEY}", "X-User-Id": USER},
)
samples = json.loads(b)["categories"]
SU = samples["bass"][0]["url"]
print(f"sample base: {SU}")

# 4. Generate
payload = {
    "beatId": beat["id"],
    "instruments": {
        "harmonic": {
            "bass": {"sampleUrl": SU},
            "lead": {"sampleUrl": SU},
            "chord": {"sampleUrl": SU},
        },
        "drums": {
            "kick": {"sampleUrl": SU},
            "snare": {"sampleUrl": SU},
            "openHihat": {"sampleUrl": SU},
            "closedHihat": {"sampleUrl": SU},
            "auxiliary": [{"sampleUrl": SU}, {"sampleUrl": SU}, {"sampleUrl": SU}],
        },
        "mixing": {"sampleUrl": SU},
        "soundEffects": {"sampleUrl": SU},
    },
    "equalizer": {
        "tempo": 124,
        "leadDensity": 7,
        "bassDensity": 8,
        "chordDensity": 5,
        "drumDensity": 9,
    },
    "lyricsPrompt": None,
    "voiceUrl": None,
    "production": None,
    "pitch": None,
    "storeStems": False,
    "language": "en",
    "desiredDurationSeconds": 30,
}
st, b = req(
    "POST",
    "/v1/tracks/generate",
    headers={"Authorization": f"Bearer {KEY}", "X-User-Id": USER},
    body=json.dumps(payload).encode(),
)
print(f"\ngenerate: HTTP {st}")
if st != 200:
    print(f"  body: {b[:400].decode()}")
    raise SystemExit(1)
job_id = json.loads(b)["jobId"]
print(f"jobId: {job_id}")

# 5. Poll
start = time.time()
for i in range(60):  # 5 min max
    time.sleep(5)
    st, b = req(
        "GET",
        f"/v1/tracks/jobs/{job_id}",
        headers={"Authorization": f"Bearer {KEY}", "X-User-Id": USER},
    )
    j = json.loads(b)
    age = int(time.time() - start)
    print(f"[{age:>3}s] status={j['status']:<11} stage={j.get('stage')}")
    if j["status"] in ("succeeded", "failed", "canceled"):
        if j["status"] == "succeeded":
            tid = j["trackId"]
            st, b = req(
                "GET",
                f"/v1/tracks/{tid}",
                headers={"Authorization": f"Bearer {KEY}", "X-User-Id": USER},
            )
            t = json.loads(b)
            print(f"  audioUrl: {t['audioUrl']}")
            with urllib.request.urlopen(t["audioUrl"], timeout=60) as r:
                audio = r.read()
            print(f"  downloaded: {len(audio)} bytes, magic={audio[:4].hex()}")
            print(f"\n✓ END-TO-END УСПЕХ — музыка сгенерирована на наших локальных URL")
        else:
            print(f"  error: {j.get('errorMessage','')[:300]}")
        break
