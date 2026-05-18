"""Shared parsing for seed data files.

Both the Alembic data-migration (`0003_music_seed_pricing.py`) and the
runtime seed-script (`run_seed.py`) reuse these parsers, so validation
stays consistent.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from app.music.enums import (
    BeatGenre,
    BillingMode,
    BillingPlatform,
    RoundingMode,
    SampleCategory,
)
from app.music.tags import validate_tags


@dataclass(frozen=True)
class BeatSeed:
    genre: str
    title: str
    audio_url: str
    duration_seconds: int | None
    bpm: int | None
    key: str | None
    preview_url: str | None
    active: bool
    sort_order: int
    meta: dict[str, Any] | None

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SampleSeed:
    category: str
    tags: list[str]
    title: str
    audio_url: str
    duration_seconds: int | None
    active: bool
    sort_order: int
    meta: dict[str, Any] | None

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PricingRuleSeed:
    provider_model: str
    billing_mode: str
    token_rate: Decimal
    rounding_mode: str
    precharge_default_units: Decimal | None
    active_from: datetime

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        # SQLAlchemy bulk_insert hands numerics through as-is.
        return d


@dataclass(frozen=True)
class TokenProductSeed:
    code: str
    platform: str
    external_product_id: str
    token_amount: int
    price_minor: int | None
    currency: str | None
    active: bool

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array at the top level")
    return data


def parse_beats(path: Path) -> list[BeatSeed]:
    rows = _read_json(path)
    parsed: list[BeatSeed] = []
    for i, row in enumerate(rows):
        try:
            genre = BeatGenre(row["genre"]).value
            seed = BeatSeed(
                genre=genre,
                title=str(row["title"]).strip(),
                audio_url=str(row["audio_url"]).strip(),
                duration_seconds=row.get("duration_seconds"),
                bpm=row.get("bpm"),
                key=row.get("key"),
                preview_url=row.get("preview_url"),
                active=bool(row.get("active", True)),
                sort_order=int(row.get("sort_order", 0)),
                meta=row.get("meta"),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}[{i}]: {exc}") from exc
        if not seed.audio_url.startswith(("http://", "https://")):
            raise ValueError(
                f"{path}[{i}]: audio_url must be http(s); got {seed.audio_url!r}"
            )
        parsed.append(seed)
    return parsed


def parse_samples(path: Path) -> list[SampleSeed]:
    rows = _read_json(path)
    parsed: list[SampleSeed] = []
    for i, row in enumerate(rows):
        try:
            category = SampleCategory(row["category"])
            tags = validate_tags(category, list(row.get("tags", [])))
            seed = SampleSeed(
                category=category.value,
                tags=tags,
                title=str(row["title"]).strip(),
                audio_url=str(row["audio_url"]).strip(),
                duration_seconds=row.get("duration_seconds"),
                active=bool(row.get("active", True)),
                sort_order=int(row.get("sort_order", 0)),
                meta=row.get("meta"),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}[{i}]: {exc}") from exc
        if not seed.audio_url.startswith(("http://", "https://")):
            raise ValueError(
                f"{path}[{i}]: audio_url must be http(s); got {seed.audio_url!r}"
            )
        parsed.append(seed)
    return parsed


def parse_pricing_rules(path: Path) -> list[PricingRuleSeed]:
    rows = _read_json(path)
    parsed: list[PricingRuleSeed] = []
    for i, row in enumerate(rows):
        try:
            seed = PricingRuleSeed(
                provider_model=str(row["provider_model"]).strip(),
                billing_mode=BillingMode(row["billing_mode"]).value,
                token_rate=Decimal(str(row["token_rate"])),
                rounding_mode=RoundingMode(row.get("rounding_mode", "ceil")).value,
                precharge_default_units=(
                    Decimal(str(row["precharge_default_units"]))
                    if row.get("precharge_default_units") is not None
                    else None
                ),
                active_from=_parse_dt(row["active_from"]),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}[{i}]: {exc}") from exc
        parsed.append(seed)
    return parsed


def parse_token_products(path: Path) -> list[TokenProductSeed]:
    rows = _read_json(path)
    parsed: list[TokenProductSeed] = []
    for i, row in enumerate(rows):
        try:
            seed = TokenProductSeed(
                code=str(row["code"]).strip(),
                platform=BillingPlatform(row["platform"]).value,
                external_product_id=str(row["external_product_id"]).strip(),
                token_amount=int(row["token_amount"]),
                price_minor=row.get("price_minor"),
                currency=row.get("currency"),
                active=bool(row.get("active", True)),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}[{i}]: {exc}") from exc
        if seed.token_amount <= 0:
            raise ValueError(f"{path}[{i}]: token_amount must be positive")
        parsed.append(seed)
    return parsed


def _parse_dt(value: str) -> datetime:
    # Allow both `...Z` and `...+00:00` per ISO 8601.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def rows_for_bulk_insert(seeds: Iterable[Any]) -> list[dict[str, Any]]:
    return [s.to_row() for s in seeds]
