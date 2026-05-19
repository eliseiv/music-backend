#!/usr/bin/env python3
"""Скачивает 3 бита на каждый жанр с Freesound, кладёт локально и пишет beats.json.

После запуска:
  1. MP3 файлы лежат в ./build/beats/{genre}_{N}.mp3 (15 файлов)
  2. app/music/seed/data/beats.json содержит 15 записей с URL
     https://appsprobek.shop/static/audio/beats/{genre}_{N}.mp3
  3. Печатает scp + reseed команды для применения на сервере

Usage:
    FREESOUND_TOKEN=... python scripts/seed_beats_3x.py
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

FREESOUND_TOKEN = os.environ["FREESOUND_TOKEN"]
DOMAIN = "appsprobek.shop"
PUBLIC_BASE = f"https://{DOMAIN}/static/audio/beats"

# (genre) → list of (title, query, bpm, key, tags)
BEATS_QUERIES: dict[str, list[tuple[str, str, int, str, list[str]]]] = {
    "electronic_dance": [
        ("House Pulse 124", "house loop 124 bpm", 124, "Am", ["house", "edm"]),
        ("Techno Driver", "techno loop 130 bpm", 130, "Dm", ["techno", "edm"]),
        ("Dub Pressure", "dubstep loop 140 bpm", 140, "Cm", ["dubstep", "drum_and_bass"]),
    ],
    "rap": [
        ("Trap Cinematic", "trap beat 90 bpm", 90, "Gm", ["trap", "phonk"]),
        ("Boom Bap Classic", "boom bap loop 85 bpm", 85, "Am", ["boom_bap", "old_school"]),
        ("Drill Tension", "drill beat loop", 140, "Em", ["drill"]),
    ],
    "lofi": [
        ("Rainy Window", "lofi hip hop loop", 75, "Cmaj7", ["lofi_hip_hop", "rainy"]),
        ("Chillhop Cafe", "chillhop loop", 80, "Fmaj7", ["chillhop", "jazz_lofi"]),
        ("Study Vinyl", "lofi study loop", 70, "Dmaj7", ["study_beats", "vinyl"]),
    ],
    "global_groove": [
        ("Afro Sunrise", "afrobeat loop", 110, "Em", ["afrobeat", "world"]),
        ("Latin Heat", "latin percussion loop", 120, "Am", ["latin", "samba"]),
        ("Reggaeton Vibes", "reggaeton loop", 95, "Dm", ["reggaeton"]),
    ],
    "relaxing_meditation": [
        ("Forest Breath", "ambient pad meditation", 60, "Dmaj", ["ambient", "nature"]),
        ("Spa Drone", "drone meditation loop", 55, "Cmaj", ["drone", "spa"]),
        ("Sleep Waves", "binaural sleep loop", 50, "Gmaj", ["binaural", "sleep"]),
    ],
}

# fal-ai/minimax-music требует reference_audio_url ≥ 10 сек
DURATION_MIN = 10
DURATION_MAX = 60
RATE_DELAY = 1.1


def freesound_search(query: str) -> list[dict]:
    flt = f"duration:[{DURATION_MIN} TO {DURATION_MAX}]"
    params = {
        "query": query,
        "filter": flt,
        "fields": "id,name,duration,previews,license,username",
        "page_size": 15,
        "token": FREESOUND_TOKEN,
    }
    url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params)
    time.sleep(RATE_DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": "music-backend-seed/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("results", []) or []
    except HTTPError as e:
        print(f"  ! freesound HTTP {e.code}: {e.read()[:200].decode()}")
        return []


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "music-backend-seed/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    build_dir = repo_root / "build" / "beats"
    build_dir.mkdir(parents=True, exist_ok=True)
    seed_path = repo_root / "app" / "music" / "seed" / "data" / "beats.json"

    used_ids: set[int] = set()
    beats_out: list[dict] = []
    attribution: list[dict] = []

    sort_order = 1
    for genre, queries in BEATS_QUERIES.items():
        print(f"\n=== {genre} ===")
        for idx, (title, query, bpm, key, tags) in enumerate(queries, start=1):
            print(f"  [{idx}] '{title}' query='{query}'")
            results = freesound_search(query)
            chosen = None
            for r in results:
                if r.get("id") and r["id"] not in used_ids:
                    used_ids.add(r["id"])
                    chosen = r
                    break
            if not chosen:
                print(f"    ! no unique result, skipping")
                continue
            preview_url = chosen["previews"]["preview-hq-mp3"]
            content = download(preview_url)
            filename = f"{genre}_{idx}.mp3"
            local = build_dir / filename
            local.write_bytes(content)
            print(f"    -> {local} ({len(content)} bytes, fs={chosen['id']})")

            beats_out.append({
                "genre": genre,
                "tags": tags,
                "title": title,
                "audio_url": f"{PUBLIC_BASE}/{filename}",
                "duration_seconds": int(chosen["duration"]),
                "bpm": bpm,
                "key": key,
                "sort_order": sort_order,
            })
            attribution.append({
                "genre": genre,
                "file": filename,
                "freesound_id": chosen["id"],
                "freesound_name": chosen["name"],
                "author": chosen.get("username"),
                "license": chosen.get("license"),
            })
            sort_order += 1

    seed_path.write_text(
        json.dumps(beats_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (seed_path.parent / "_freesound_attribution_beats.json").write_text(
        json.dumps(attribution, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n=== DONE ===")
    print(f"beats: {len(beats_out)}")
    print(f"files: {build_dir}")
    print(f"seed:  {seed_path}")
    print()
    print("Next: scp + reseed на сервере:")
    print(f"  scp -i ~/.ssh/music_backend_deploy build/beats/*.mp3 root@5.39.19.231:/opt/music-backend/static/audio/beats/")
    print(f"  ssh root@5.39.19.231 'cd /opt/music-backend && rm -f static/audio/beats/{{electronic_dance,rap,lofi,global_groove,relaxing_meditation}}.mp3'")
    # reseed = git push (CI задеплоит beats.json) + docker exec api python -m app.music.seed.run_seed


if __name__ == "__main__":
    main()
