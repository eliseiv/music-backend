#!/usr/bin/env python3
"""Скачивает по 3 sample-файла на пару (категория, тег) с Freesound.

Структура:
  - harmonic_bass × 14 тегов × 3 = 42 (sort_order 1..42)
  - harmonic_lead × 14 × 3        = 42
  - harmonic_chord × 14 × 3       = 42
  - drums_kick × 7 × 3            = 21
  - drums_snare × 7 × 3           = 21
  - drums_closed_hihat × 7 × 3    = 21
  - drums_open_hihat × 7 × 3      = 21
  - drums_auxiliary × 7 × 3       = 21
  - mixing (без тегов)            = 3
  - sound_effects (без тегов)     = 3
  Итого: 237 файлов.

После запуска:
  - MP3 в build/samples/{category}_{sort_order}.mp3
  - app/music/seed/data/samples.json — 237 записей
  - app/music/seed/data/_freesound_attribution_samples.json — авторы/лицензии
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
PUBLIC_BASE = f"https://{DOMAIN}/static/audio/samples"
PER_TAG = 3  # сколько разных sample на (категория, тег)
RATE_DELAY = 1.1


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

HARMONIC_CATEGORIES = {
    "harmonic_bass": "bass",
    "harmonic_lead": "lead",
    "harmonic_chord": "chord",
}

DRUM_TAG_QUERIES = {
    "all_drums": "drum one shot",
    "acoustic": "acoustic drum kit",
    "dusty": "dusty lofi drum",
    "edm": "edm drum",
    "experimental": "experimental drum",
    "trap_808": "808 trap drum",
    "vintage_electronic": "vintage analog drum machine",
}

DRUM_CATEGORIES = {
    "drums_kick": "kick",
    "drums_snare": "snare",
    "drums_closed_hihat": "closed hi hat",
    "drums_open_hihat": "open hi hat",
    "drums_auxiliary": "percussion",
}

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


def freesound_search(query: str, *, duration_min: float, duration_max: float, n: int = 15) -> list[dict]:
    flt = f"duration:[{duration_min} TO {duration_max}]"
    params = {
        "query": query,
        "filter": flt,
        "fields": "id,name,duration,previews,license,username",
        "page_size": n,
        "token": FREESOUND_TOKEN,
    }
    url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params)
    time.sleep(RATE_DELAY)
    req = urllib.request.Request(url, headers={"User-Agent": "music-backend-seed/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("results", []) or []
    except HTTPError as e:
        print(f"  ! HTTP {e.code}: {e.read()[:150].decode()}")
        return []
    except Exception as e:
        print(f"  ! err: {e}")
        return []


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "music-backend-seed/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def pick_n(results: list[dict], used: set[int], n: int) -> list[dict]:
    chosen = []
    for r in results:
        if len(chosen) >= n:
            break
        if r.get("id") and r["id"] not in used:
            used.add(r["id"])
            chosen.append(r)
    return chosen


def save(content: bytes, build_dir: Path, filename: str) -> Path:
    p = build_dir / filename
    p.write_bytes(content)
    return p


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    build_dir = repo_root / "build" / "samples"
    build_dir.mkdir(parents=True, exist_ok=True)
    seed_path = repo_root / "app" / "music" / "seed" / "data" / "samples.json"
    attr_path = repo_root / "app" / "music" / "seed" / "data" / "_freesound_attribution_samples.json"

    used_ids: set[int] = set()
    samples_out: list[dict] = []
    attribution: list[dict] = []

    # --- HARMONIC ---
    for category, cat_q in HARMONIC_CATEGORIES.items():
        cat_sort = 1
        print(f"\n=== {category} ===")
        for tag, tag_q in HARMONIC_TAG_QUERIES.items():
            full_q = f"{cat_q} {tag_q}"
            print(f"  {tag:25s} q='{full_q}'")
            # Берём 3 разных из выдачи; если не хватает — fallback на tag_q или cat_q
            picks: list[dict] = []
            for q in (full_q, tag_q, cat_q):
                if len(picks) >= PER_TAG:
                    break
                more = pick_n(
                    freesound_search(q, duration_min=0.5, duration_max=10, n=15),
                    used_ids,
                    PER_TAG - len(picks),
                )
                picks.extend(more)
            if not picks:
                print(f"    ! ничего не нашлось")
                continue
            tags_field = ["all_instruments"] if tag == "all_instruments" else ["all_instruments", tag]
            for chosen in picks:
                try:
                    content = download(chosen["previews"]["preview-hq-mp3"])
                except Exception as e:
                    print(f"    ! download fail fs={chosen['id']}: {e}")
                    continue
                filename = f"{category}_{cat_sort}.mp3"
                save(content, build_dir, filename)
                samples_out.append({
                    "category": category,
                    "tags": tags_field,
                    "title": (chosen.get("name") or f"{category}_{cat_sort}")[:60],
                    "audio_url": f"{PUBLIC_BASE}/{filename}",
                    "duration_seconds": max(1, int(chosen.get("duration") or 1)),
                    "sort_order": cat_sort,
                })
                attribution.append({
                    "category": category,
                    "tag": tag,
                    "file": filename,
                    "freesound_id": chosen["id"],
                    "author": chosen.get("username"),
                    "license": chosen.get("license"),
                })
                print(f"    [{cat_sort:2}] fs={chosen['id']} {chosen.get('name','')[:50]}")
                cat_sort += 1

    # --- DRUMS ---
    for category, cat_q in DRUM_CATEGORIES.items():
        cat_sort = 1
        print(f"\n=== {category} ===")
        for tag, tag_q in DRUM_TAG_QUERIES.items():
            full_q = f"{cat_q} {tag_q.split()[0]}"
            print(f"  {tag:25s} q='{full_q}'")
            picks: list[dict] = []
            for q in (full_q, f"{cat_q} {tag_q}", cat_q):
                if len(picks) >= PER_TAG:
                    break
                more = pick_n(
                    freesound_search(q, duration_min=0.1, duration_max=5, n=15),
                    used_ids,
                    PER_TAG - len(picks),
                )
                picks.extend(more)
            if not picks:
                print(f"    ! ничего не нашлось")
                continue
            tags_field = ["all_drums"] if tag == "all_drums" else ["all_drums", tag]
            for chosen in picks:
                try:
                    content = download(chosen["previews"]["preview-hq-mp3"])
                except Exception as e:
                    print(f"    ! download fail fs={chosen['id']}: {e}")
                    continue
                filename = f"{category}_{cat_sort}.mp3"
                save(content, build_dir, filename)
                samples_out.append({
                    "category": category,
                    "tags": tags_field,
                    "title": (chosen.get("name") or f"{category}_{cat_sort}")[:60],
                    "audio_url": f"{PUBLIC_BASE}/{filename}",
                    "duration_seconds": max(1, int(chosen.get("duration") or 1)),
                    "sort_order": cat_sort,
                })
                attribution.append({
                    "category": category,
                    "tag": tag,
                    "file": filename,
                    "freesound_id": chosen["id"],
                    "author": chosen.get("username"),
                    "license": chosen.get("license"),
                })
                print(f"    [{cat_sort:2}] fs={chosen['id']} {chosen.get('name','')[:50]}")
                cat_sort += 1

    # --- MISC (mixing + sound_effects, без тегов) ---
    for category, entries in MISC_QUERIES.items():
        print(f"\n=== {category} ===")
        for i, (title, q) in enumerate(entries, start=1):
            picks = pick_n(
                freesound_search(q, duration_min=0.5, duration_max=8, n=15),
                used_ids,
                1,
            )
            if not picks:
                continue
            chosen = picks[0]
            try:
                content = download(chosen["previews"]["preview-hq-mp3"])
            except Exception as e:
                print(f"  ! download fail: {e}")
                continue
            filename = f"{category}_{i}.mp3"
            save(content, build_dir, filename)
            samples_out.append({
                "category": category,
                "tags": [],
                "title": title,
                "audio_url": f"{PUBLIC_BASE}/{filename}",
                "duration_seconds": max(1, int(chosen.get("duration") or 1)),
                "sort_order": i,
            })
            attribution.append({
                "category": category,
                "tag": None,
                "file": filename,
                "freesound_id": chosen["id"],
                "author": chosen.get("username"),
                "license": chosen.get("license"),
            })
            print(f"  [{i}] {title} fs={chosen['id']}")

    seed_path.write_text(
        json.dumps(samples_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    attr_path.write_text(
        json.dumps(attribution, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"\n=== DONE ===")
    print(f"samples: {len(samples_out)}")
    print(f"files:   {build_dir}")
    print(f"seed:    {seed_path}")

    # Распределение по категориям
    from collections import Counter
    by_cat = Counter(s["category"] for s in samples_out)
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat:25s} {n}")


if __name__ == "__main__":
    main()
