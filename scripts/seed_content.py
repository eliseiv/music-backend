#!/usr/bin/env python3
"""Загружает реальные beats + samples с Freesound в fal storage и БД.

1. Ищет на Freesound подходящие звуки по жанру/тегу.
2. Скачивает preview-hq-mp3 (~128 kbps, не требует OAuth).
3. Загружает каждый через наш `/v1/uploads/voice` → получает fal-storage URL.
4. Пишет обновлённые `beats.json` и `samples.json`.
5. Печатает SSH-команды для применения seed в БД.

Usage:
    FREESOUND_TOKEN=... \
    BASE_URL=https://appsprobek.shop \
    API_KEY=... \
    python scripts/seed_content.py

Free dependencies: stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

FREESOUND_TOKEN = os.environ["FREESOUND_TOKEN"]
BASE_URL = os.environ.get("BASE_URL", "https://appsprobek.shop").rstrip("/")
API_KEY = os.environ["API_KEY"]
X_USER_ID = "seed-loader"

# Пауза между Freesound запросами — лимит 60 req/min.
FREESOUND_RATE_DELAY = 1.1

# ============================================================
# КОНФИГИ
# ============================================================

# (genre_enum) → (title, search_query, bpm, key)
BEATS_QUERIES = [
    ("electronic_dance", "Pulse 124", "house loop 124 bpm", 124, "Am"),
    ("rap", "Trap Cinematic", "trap beat 90 bpm", 90, "Gm"),
    ("lofi", "Rainy Window", "lofi hip hop loop", 75, "Cmaj7"),
    ("global_groove", "Afro Sunrise", "afro percussion loop", 110, "Em"),
    ("relaxing_meditation", "Forest Breath", "ambient pad meditation", 60, "Dmaj"),
]
BEAT_DURATION_MIN = 10  # fal-ai/minimax-music требует ≥10 сек
BEAT_DURATION_MAX = 30

# Harmonic-теги (14) → freesound search query (на жанровый звук тега).
HARMONIC_TAG_QUERIES = {
    "all_instruments": "synth pluck",
    "acoustic_guitars": "acoustic guitar fingerpicking",
    "global_ensemble": "world percussion ensemble",
    "acoustic_instruments": "acoustic piano short",
    "chill_keys": "rhodes electric piano",
    "seventies_fusion": "fusion electric piano",
    "jazz_trio": "upright bass jazz",
    "rock_n_roll": "electric guitar rock",
    "soft_rock": "soft rock guitar arpeggio",
    "classical_strings": "string ensemble pad",
    "synth_haven": "analog synth pad warm",
    "smooth_pop": "smooth pop synth chord",
    "carolina_trap_set": "trap synth pluck",
    "brass_and_winds": "brass section stab",
}

# Harmonic-категории + дополнительный префикс к query тега.
# (даёт разнообразие — bass jazz vs lead jazz и т.п.)
HARMONIC_CATEGORIES = {
    "harmonic_bass": "bass",
    "harmonic_lead": "lead",
    "harmonic_chord": "chord",
}

# Drum-теги (7).
DRUM_TAG_QUERIES = {
    "all_drums": "drum one shot",
    "acoustic": "acoustic drum kit",
    "dusty": "dusty lofi drum",
    "edm": "edm drum",
    "experimental": "experimental drum",
    "trap_808": "808 trap drum",
    "vintage_electronic": "vintage analog drum machine",
}

# Drum-категории + базовый query.
DRUM_CATEGORIES = {
    "drums_kick": "kick",
    "drums_snare": "snare",
    "drums_closed_hihat": "closed hi hat",
    "drums_open_hihat": "open hi hat",
    "drums_auxiliary": "percussion",
}

# Без тегов — простые запросы.
MISC_QUERIES = {
    "mixing": [
        ("Master Saturator", "tape saturation"),
        ("Bus Compressor Glue", "compressor sound"),
        ("Reverb Tail", "reverb hall tail"),
    ],
    "sound_effects": [
        ("Riser FX", "riser sweep"),
        ("Vinyl Crackle", "vinyl crackle"),
        ("Atmospheric Whoosh", "whoosh transition"),
    ],
}

# ============================================================
# FREESOUND API
# ============================================================

def _freesound_request(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "music-backend-seed/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def freesound_search(
    query: str, *, duration_min: float, duration_max: float, n: int = 15
) -> list[dict]:
    """Возвращает первые N результатов по запросу с фильтром по длительности."""
    flt = f"duration:[{duration_min} TO {duration_max}]"
    params = {
        "query": query,
        "filter": flt,
        "fields": "id,name,duration,previews,license,username",
        "page_size": n,
        "token": FREESOUND_TOKEN,
    }
    url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params)
    time.sleep(FREESOUND_RATE_DELAY)
    try:
        data = _freesound_request(url)
        return data.get("results", []) or []
    except HTTPError as e:
        print(f"  ! Freesound {e.code}: {e.read()[:200].decode('utf-8','ignore')}")
        return []
    except Exception as e:
        print(f"  ! Freesound error: {e}")
        return []


def download_preview(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "music-backend-seed/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# ============================================================
# Upload в fal storage через наш /v1/uploads/voice
# ============================================================

def upload_to_fal(content: bytes, filename: str) -> str:
    boundary = "----SEED-" + os.urandom(8).hex()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{BASE_URL}/v1/uploads/voice",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "X-User-Id": X_USER_ID,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["voiceUrl"]
    except HTTPError as e:
        raise RuntimeError(
            f"Upload failed: HTTP {e.code} — {e.read()[:300].decode('utf-8','ignore')}"
        )


# ============================================================
# Helpers
# ============================================================

def pick_unique(results: list[dict], used: set[int]) -> dict | None:
    for r in results:
        if r.get("id") and r["id"] not in used:
            used.add(r["id"])
            return r
    return None


def fetch_and_upload(
    query: str,
    *,
    duration_min: float,
    duration_max: float,
    filename: str,
    used: set[int],
    fallback_queries: list[str] | None = None,
) -> tuple[str | None, dict | None]:
    """Возвращает (fal_url, freesound_metadata). При неуспехе — (None, None)."""
    for q in [query] + (fallback_queries or []):
        results = freesound_search(
            q, duration_min=duration_min, duration_max=duration_max
        )
        chosen = pick_unique(results, used)
        if chosen:
            try:
                preview_url = chosen["previews"]["preview-hq-mp3"]
                content = download_preview(preview_url)
                fal_url = upload_to_fal(content, filename)
                return fal_url, chosen
            except Exception as e:
                print(f"    ! download/upload error for fs={chosen['id']}: {e}")
                continue
    return None, None


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    seed_dir = Path(__file__).resolve().parent.parent / "app/music/seed/data"
    used: set[int] = set()
    meta_log: list[dict] = []  # attribution log

    beats_out: list[dict] = []
    samples_out: list[dict] = []

    # ----- BEATS -----
    print(f"\n[1/4] Beats ({len(BEATS_QUERIES)} жанров)...")
    for sort_order, (genre, title, query, bpm, key) in enumerate(
        BEATS_QUERIES, start=1
    ):
        print(f"  {genre}: query='{query}'")
        fal_url, meta = fetch_and_upload(
            query,
            duration_min=BEAT_DURATION_MIN,
            duration_max=BEAT_DURATION_MAX,
            filename=f"beat_{genre}.mp3",
            used=used,
            fallback_queries=[query.split()[0] + " loop"],
        )
        if fal_url is None:
            print(f"    SKIP {genre} (no result)")
            continue
        print(f"    ok: fs_id={meta['id']} dur={meta['duration']:.1f}s")
        beats_out.append({
            "genre": genre,
            "title": title,
            "audio_url": fal_url,
            "duration_seconds": int(meta["duration"]),
            "bpm": bpm,
            "key": key,
            "sort_order": sort_order,
        })
        meta_log.append({
            "kind": "beat",
            "genre": genre,
            "source": f"freesound.org/s/{meta['id']}",
            "author": meta.get("username"),
            "license": meta.get("license"),
        })

    # ----- HARMONIC SAMPLES (14 tags × 3 categories) -----
    print(f"\n[2/4] Harmonic samples ({len(HARMONIC_CATEGORIES)*len(HARMONIC_TAG_QUERIES)} = {len(HARMONIC_CATEGORIES)*len(HARMONIC_TAG_QUERIES)})...")
    for category, base_q in HARMONIC_CATEGORIES.items():
        cat_sort = 1
        for tag, tag_q in HARMONIC_TAG_QUERIES.items():
            full_q = f"{base_q} {tag_q}"
            print(f"  {category}/{tag}: '{full_q}'")
            fal_url, meta = fetch_and_upload(
                full_q,
                duration_min=0.5,
                duration_max=10,
                filename=f"{category}_{tag}.mp3",
                used=used,
                fallback_queries=[tag_q, base_q],
            )
            if fal_url is None:
                print(f"    SKIP")
                continue
            title = (meta["name"] or f"{category} {tag}")[:60]
            tags = ["all_instruments"]
            if tag != "all_instruments":
                tags.append(tag)
            samples_out.append({
                "category": category,
                "tags": tags,
                "title": title,
                "audio_url": fal_url,
                "duration_seconds": max(1, int(meta["duration"])),
                "sort_order": cat_sort,
            })
            cat_sort += 1
            meta_log.append({
                "kind": "sample",
                "category": category,
                "tag": tag,
                "source": f"freesound.org/s/{meta['id']}",
                "author": meta.get("username"),
                "license": meta.get("license"),
            })

    # ----- DRUM SAMPLES (7 tags × 5 categories) -----
    print(f"\n[3/4] Drum samples ({len(DRUM_CATEGORIES)*len(DRUM_TAG_QUERIES)})...")
    for category, base_q in DRUM_CATEGORIES.items():
        cat_sort = 1
        for tag, tag_q in DRUM_TAG_QUERIES.items():
            # для one-shot хитов берём первое слово тег-query
            full_q = f"{base_q} {tag_q.split()[0]}"
            print(f"  {category}/{tag}: '{full_q}'")
            fal_url, meta = fetch_and_upload(
                full_q,
                duration_min=0.1,
                duration_max=5,
                filename=f"{category}_{tag}.mp3",
                used=used,
                fallback_queries=[base_q + " " + tag, base_q],
            )
            if fal_url is None:
                print(f"    SKIP")
                continue
            title = (meta["name"] or f"{category} {tag}")[:60]
            tags = ["all_drums"]
            if tag != "all_drums":
                tags.append(tag)
            samples_out.append({
                "category": category,
                "tags": tags,
                "title": title,
                "audio_url": fal_url,
                "duration_seconds": max(1, int(meta["duration"])),
                "sort_order": cat_sort,
            })
            cat_sort += 1
            meta_log.append({
                "kind": "sample",
                "category": category,
                "tag": tag,
                "source": f"freesound.org/s/{meta['id']}",
                "author": meta.get("username"),
                "license": meta.get("license"),
            })

    # ----- MIXING + SOUND_EFFECTS (без тегов) -----
    print(f"\n[4/4] Mixing + sound_effects...")
    for category, entries in MISC_QUERIES.items():
        for i, (title, query) in enumerate(entries, start=1):
            print(f"  {category}/{title}: '{query}'")
            fal_url, meta = fetch_and_upload(
                query,
                duration_min=0.5,
                duration_max=8,
                filename=f"{category}_{i}.mp3",
                used=used,
            )
            if fal_url is None:
                print(f"    SKIP")
                continue
            samples_out.append({
                "category": category,
                "tags": [],
                "title": title,
                "audio_url": fal_url,
                "duration_seconds": max(1, int(meta["duration"])),
                "sort_order": i,
            })
            meta_log.append({
                "kind": "sample",
                "category": category,
                "source": f"freesound.org/s/{meta['id']}",
                "author": meta.get("username"),
                "license": meta.get("license"),
            })

    # ----- Write JSON -----
    print(f"\n=== summary ===")
    print(f"beats:   {len(beats_out)}")
    print(f"samples: {len(samples_out)}")

    (seed_dir / "beats.json").write_text(
        json.dumps(beats_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (seed_dir / "samples.json").write_text(
        json.dumps(samples_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (seed_dir / "_freesound_attribution.json").write_text(
        json.dumps(meta_log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nWrote:")
    print(f"  {seed_dir / 'beats.json'}")
    print(f"  {seed_dir / 'samples.json'}")
    print(f"  {seed_dir / '_freesound_attribution.json'}  (license credits)")

    print(f"\n=== Next ===")
    print(f"  1. git add app/music/seed/data/*.json scripts/seed_content.py")
    print(f"  2. git commit -m 'seed: real beats + samples via Freesound + fal storage'")
    print(f"  3. git push  (CI задеплоит)")
    print(f"  4. На сервере прогнать reseed:")
    print(f"     ssh root@5.39.19.231 'cd /opt/music-backend && \\")
    print(f"       docker compose -f docker-compose.prod.yml exec -T postgres psql -U music -d music \\")
    print(f"         -c \"DELETE FROM samples; DELETE FROM beats;\" && \\")
    print(f"       docker compose -f docker-compose.prod.yml exec -T api python -m app.music.seed.run_seed \\")
    print(f"         --beats app/music/seed/data/beats.json \\")
    print(f"         --samples app/music/seed/data/samples.json'")


if __name__ == "__main__":
    main()
