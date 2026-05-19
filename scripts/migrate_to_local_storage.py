#!/usr/bin/env python3
"""Миграция: скачать все beats + samples из fal storage на локальный диск.

Запускается ВНУТРИ контейнера api на сервере:
    docker compose -f docker-compose.prod.yml exec api python -m scripts.migrate_to_local_storage

Что делает:
1. Подключается к БД.
2. Для каждого бита: скачивает audio_url → кладёт в /var/www/static/audio/beats/<genre>.mp3
3. Для каждого sample: → /var/www/static/audio/samples/<category>_<sort_order>.mp3
4. UPDATE'ит audio_url в БД на https://<DOMAIN>/static/audio/beats/<genre>.mp3

Идемпотентен: если файл уже скачан и URL в БД уже наш — skip.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Куда кладём (в контейнере nginx видит как /var/www/static/, на хосте — /opt/music-backend/static/).
# Скрипт запускается в контейнере api, который НЕ имеет mount /var/www/static.
# Поэтому мы маунтим тот же volume и в api — либо записываем напрямую через хост.
# Простейшее решение: писать в /app/static и потом руками cp на хост.
# Лучше — запустить скрипт прямо на хосте, не в контейнере.
STATIC_ROOT = Path(os.environ.get("STATIC_ROOT", "/opt/music-backend/static"))
DOMAIN = os.environ.get("DEPLOY_DOMAIN", "appsprobek.shop")
PUBLIC_BASE = f"https://{DOMAIN}/static/audio"
DATABASE_URL = os.environ["DATABASE_URL"]


async def main() -> None:
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)
    (STATIC_ROOT / "audio" / "beats").mkdir(parents=True, exist_ok=True)
    (STATIC_ROOT / "audio" / "samples").mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(DATABASE_URL)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        async with engine.begin() as conn:
            # ---- BEATS ----
            beats_rows = (
                await conn.execute(
                    text("SELECT id, genre::text, audio_url FROM beats ORDER BY genre")
                )
            ).all()
            print(f"\n[BEATS] {len(beats_rows)} rows")
            for row in beats_rows:
                bid, genre, src = row
                if src.startswith(PUBLIC_BASE):
                    print(f"  skip {genre}: already local")
                    continue
                # filename — детерминированный, не дублируется
                filename = f"{genre}.mp3"
                local = STATIC_ROOT / "audio" / "beats" / filename
                if not local.exists():
                    print(f"  download {genre}: {src}")
                    try:
                        r = await client.get(src)
                        r.raise_for_status()
                        local.write_bytes(r.content)
                        print(f"    -> {local} ({len(r.content)} bytes)")
                    except Exception as e:
                        print(f"    ! download failed: {e}")
                        continue
                else:
                    print(f"  reuse {filename} ({local.stat().st_size} bytes)")
                new_url = f"{PUBLIC_BASE}/beats/{filename}"
                await conn.execute(
                    text("UPDATE beats SET audio_url=:url WHERE id=:id"),
                    {"url": new_url, "id": bid},
                )
                print(f"    DB -> {new_url}")

            # ---- SAMPLES ----
            samples_rows = (
                await conn.execute(
                    text(
                        "SELECT id, category::text, sort_order, audio_url "
                        "FROM samples ORDER BY category, sort_order"
                    )
                )
            ).all()
            print(f"\n[SAMPLES] {len(samples_rows)} rows")
            for row in samples_rows:
                sid, cat, sort_order, src = row
                if src.startswith(PUBLIC_BASE):
                    continue
                filename = f"{cat}_{sort_order}.mp3"
                local = STATIC_ROOT / "audio" / "samples" / filename
                if not local.exists():
                    try:
                        r = await client.get(src)
                        r.raise_for_status()
                        local.write_bytes(r.content)
                    except Exception as e:
                        print(f"  ! {filename} failed: {e}")
                        continue
                new_url = f"{PUBLIC_BASE}/samples/{filename}"
                await conn.execute(
                    text("UPDATE samples SET audio_url=:url WHERE id=:id"),
                    {"url": new_url, "id": sid},
                )

    await engine.dispose()

    # Stats
    total_size = sum(
        f.stat().st_size for f in STATIC_ROOT.rglob("*.mp3") if f.is_file()
    )
    total_count = sum(1 for f in STATIC_ROOT.rglob("*.mp3") if f.is_file())
    print(f"\n=== Done ===")
    print(f"Files: {total_count}")
    print(f"Size:  {total_size / 1024 / 1024:.1f} MB")
    print(f"Root:  {STATIC_ROOT}")
    print(f"URL:   {PUBLIC_BASE}/...")


if __name__ == "__main__":
    asyncio.run(main())
