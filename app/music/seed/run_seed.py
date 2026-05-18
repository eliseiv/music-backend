"""Idempotent seed loader for beats and samples.

Usage (inside the api container):
    python -m app.music.seed.run_seed \\
        --beats app/music/seed/data/beats.json \\
        --samples app/music/seed/data/samples.json

Both files are optional. Existing rows with the same `audio_url` are updated
in-place via INSERT ... ON CONFLICT DO UPDATE.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.music.seed import importers

logger = logging.getLogger("app.music.seed")

BEATS_UPSERT = text(
    """
    INSERT INTO beats (
        genre, title, audio_url, duration_seconds, bpm, key,
        preview_url, active, sort_order, meta
    )
    VALUES (
        CAST(:genre AS beat_genre),
        :title,
        :audio_url,
        :duration_seconds,
        :bpm,
        :key,
        :preview_url,
        :active,
        :sort_order,
        CAST(:meta AS jsonb)
    )
    ON CONFLICT (audio_url) DO UPDATE SET
        genre = EXCLUDED.genre,
        title = EXCLUDED.title,
        duration_seconds = EXCLUDED.duration_seconds,
        bpm = EXCLUDED.bpm,
        key = EXCLUDED.key,
        preview_url = EXCLUDED.preview_url,
        active = EXCLUDED.active,
        sort_order = EXCLUDED.sort_order,
        meta = EXCLUDED.meta,
        updated_at = now()
    """
)

SAMPLES_UPSERT = text(
    """
    INSERT INTO samples (
        category, tags, title, audio_url, duration_seconds,
        active, sort_order, meta
    )
    VALUES (
        CAST(:category AS sample_category),
        :tags,
        :title,
        :audio_url,
        :duration_seconds,
        :active,
        :sort_order,
        CAST(:meta AS jsonb)
    )
    ON CONFLICT (audio_url) DO UPDATE SET
        category = EXCLUDED.category,
        tags = EXCLUDED.tags,
        title = EXCLUDED.title,
        duration_seconds = EXCLUDED.duration_seconds,
        active = EXCLUDED.active,
        sort_order = EXCLUDED.sort_order,
        meta = EXCLUDED.meta,
        updated_at = now()
    """
)


def _row_for_beat(seed: importers.BeatSeed) -> dict:
    row = seed.to_row()
    if row.get("meta") is not None:
        import json

        row["meta"] = json.dumps(row["meta"])
    return row


def _row_for_sample(seed: importers.SampleSeed) -> dict:
    row = seed.to_row()
    # asyncpg expects a Python list for text[] columns.
    row["tags"] = list(row["tags"])
    if row.get("meta") is not None:
        import json

        row["meta"] = json.dumps(row["meta"])
    return row


async def _run(engine: AsyncEngine, *, beats_path: Path | None, samples_path: Path | None) -> None:
    async with engine.begin() as conn:
        if beats_path:
            beats = importers.parse_beats(beats_path)
            for seed in beats:
                await conn.execute(BEATS_UPSERT, _row_for_beat(seed))
            logger.info("Upserted %d beats from %s", len(beats), beats_path)

        if samples_path:
            samples = importers.parse_samples(samples_path)
            for seed in samples:
                await conn.execute(SAMPLES_UPSERT, _row_for_sample(seed))
            logger.info("Upserted %d samples from %s", len(samples), samples_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Seed beats and samples (idempotent).")
    parser.add_argument("--beats", type=Path, default=None)
    parser.add_argument("--samples", type=Path, default=None)
    args = parser.parse_args()

    if not args.beats and not args.samples:
        parser.error("Specify at least one of --beats / --samples")

    settings = get_settings()

    async def runner() -> None:
        engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
        try:
            await _run(
                engine, beats_path=args.beats, samples_path=args.samples
            )
        finally:
            await engine.dispose()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
