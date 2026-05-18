"""Хелперы для integration-тестов music-эндпоинтов."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.music.enums import (
    BillingMode,
    BillingProvider,
    RoundingMode,
    SubscriptionStatus,
    TokenLedgerKind,
)
from app.music.repositories.subscriptions import SubscriptionsRepository
from app.music.repositories.users import MusicUsersRepository
from app.music.services.wallet_service import WalletService


@pytest_asyncio.fixture
async def seed_pricing(engine):
    """Заливаем минимальный pricing rule (per_track, 1 токен).

    Teardown не нужен — `_truncate_music` в session conftest почистит
    все таблицы CASCADE.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO pricing_rules (
                    provider_model, billing_mode, token_rate, rounding_mode,
                    precharge_default_units, active_from
                ) VALUES (
                    'fal-ai/minimax-music',
                    CAST('per_track' AS pricing_billing_mode),
                    1,
                    CAST('ceil' AS rounding_mode),
                    NULL,
                    '2026-01-01T00:00:00Z'
                )
                """
            )
        )
    yield


@pytest_asyncio.fixture
async def seed_beats(engine):
    """Один активный бит для генерации."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO beats (
                    genre, title, audio_url, duration_seconds, bpm, key,
                    sort_order, active
                ) VALUES (
                    CAST('electronic_dance' AS beat_genre),
                    'Test Beat',
                    'https://example.test/beat.mp3',
                    30,
                    124,
                    'Am',
                    1,
                    true
                )
                RETURNING id
                """
            )
        )
        beat_id = result.scalar()
    yield str(beat_id)


@pytest_asyncio.fixture
async def seed_samples(engine):
    """Минимальный набор sample'ов для всех 10 категорий."""
    async with engine.begin() as conn:
        for cat, tags, title in [
            ("harmonic_bass", ["all_instruments"], "Bass1"),
            ("harmonic_lead", ["all_instruments"], "Lead1"),
            ("harmonic_chord", ["all_instruments"], "Chord1"),
            ("drums_kick", ["all_drums"], "Kick1"),
            ("drums_snare", ["all_drums"], "Snare1"),
            ("drums_open_hihat", ["all_drums"], "OHat1"),
            ("drums_closed_hihat", ["all_drums"], "CHat1"),
            ("drums_auxiliary", ["all_drums"], "Aux1"),
            ("mixing", [], "Mix1"),
            ("sound_effects", [], "SFX1"),
        ]:
            await conn.execute(
                text(
                    """
                    INSERT INTO samples (
                        category, tags, title, audio_url, duration_seconds,
                        active, sort_order
                    ) VALUES (
                        CAST(:cat AS sample_category),
                        :tags,
                        :title,
                        :url,
                        2,
                        true,
                        1
                    )
                    """
                ),
                {
                    "cat": cat,
                    "tags": tags,
                    "title": title,
                    "url": f"https://example.test/{cat}.wav",
                },
            )
    yield


@pytest_asyncio.fixture
async def seed_token_products(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO token_products (
                    code, platform, external_product_id, token_amount,
                    price_minor, currency, active
                ) VALUES (
                    'tokens_10',
                    CAST('adapty' AS billing_platform),
                    'com.test.tokens_10',
                    10,
                    99,
                    'USD',
                    true
                )
                """
            )
        )
    yield


@pytest_asyncio.fixture
def make_user_with_subscription(engine):
    """Factory: создаёт user + active subscription + tokens."""
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def _make(
        external_id: str,
        *,
        tokens: int = 10,
        days_left: int = 30,
        status: SubscriptionStatus = SubscriptionStatus.active,
    ) -> str:
        async with sm() as session:
            async with session.begin():
                users = MusicUsersRepository(session)
                user = await users.get_or_create(external_id=external_id)
                user_id = user.id
                if status is not None:
                    subs = SubscriptionsRepository(session)
                    state = await subs.ensure_exists(user_id)
                    state.status = status
                    state.provider = BillingProvider.adapty
                    state.product_external_id = "premium_monthly"
                    state.expires_at = datetime.now(tz=timezone.utc) + timedelta(
                        days=days_left
                    )
        if tokens > 0:
            wallet = WalletService(sm)
            await wallet.credit(
                user_id=user_id,
                amount=tokens,
                kind=TokenLedgerKind.credit_purchase,
                ref_type="test_setup",
                ref_id=f"setup-{external_id}",
            )
        return external_id

    return _make


def build_generate_payload(beat_id: str, *, voice_url: str | None = None) -> dict[str, Any]:
    """Минимальный валидный payload для POST /v1/tracks/generate."""
    payload = {
        "beatId": beat_id,
        "instruments": {
            "harmonic": {
                "bass": {"sampleUrl": "https://example.test/harmonic_bass.wav"},
                "lead": {"sampleUrl": "https://example.test/harmonic_lead.wav"},
                "chord": {"sampleUrl": "https://example.test/harmonic_chord.wav"},
            },
            "drums": {
                "kick": {"sampleUrl": "https://example.test/drums_kick.wav"},
                "snare": {"sampleUrl": "https://example.test/drums_snare.wav"},
                "openHihat": {"sampleUrl": "https://example.test/drums_open_hihat.wav"},
                "closedHihat": {"sampleUrl": "https://example.test/drums_closed_hihat.wav"},
                "auxiliary": [
                    {"sampleUrl": "https://example.test/drums_auxiliary.wav"},
                    {"sampleUrl": "https://example.test/drums_auxiliary.wav"},
                    {"sampleUrl": "https://example.test/drums_auxiliary.wav"},
                ],
            },
            "mixing": {"sampleUrl": "https://example.test/mixing.wav"},
            "soundEffects": {"sampleUrl": "https://example.test/sound_effects.wav"},
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
    return payload
