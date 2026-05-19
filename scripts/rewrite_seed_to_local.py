#!/usr/bin/env python3
"""Перепишет beats.json и samples.json — заменит fal-storage URL на
наши https://appsprobek.shop/static/audio/... URL'ы.

Запускается ЛОКАЛЬНО (на ноутбуке) после миграции файлов на сервер,
чтобы seed-data в репо отражал актуальные URL.
"""
from __future__ import annotations

import json
from pathlib import Path

DOMAIN = "appsprobek.shop"
SEED_DIR = Path(__file__).resolve().parent.parent / "app/music/seed/data"

beats = json.loads((SEED_DIR / "beats.json").read_text(encoding="utf-8"))
for b in beats:
    b["audio_url"] = f"https://{DOMAIN}/static/audio/beats/{b['genre']}.mp3"

samples = json.loads((SEED_DIR / "samples.json").read_text(encoding="utf-8"))
for s in samples:
    s["audio_url"] = (
        f"https://{DOMAIN}/static/audio/samples/{s['category']}_{s['sort_order']}.mp3"
    )

(SEED_DIR / "beats.json").write_text(
    json.dumps(beats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
(SEED_DIR / "samples.json").write_text(
    json.dumps(samples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)

print(f"beats.json:   {len(beats)} entries → https://{DOMAIN}/static/audio/beats/")
print(f"samples.json: {len(samples)} entries → https://{DOMAIN}/static/audio/samples/")
