from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.errors import PricingRuleMissing
from app.music.enums import BillingMode, RoundingMode
from app.music.models import PricingRule
from app.music.services.pricing_service import PricingService


@pytest_asyncio.fixture
async def reset_pricing(engine):
    """Clean pricing_rules between tests."""
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM pricing_rules"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM pricing_rules"))


@pytest_asyncio.fixture
async def pricing_service(engine, reset_pricing):
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return PricingService(sm)


def _insert_rule_sql(**kwargs):
    return text(
        """
        INSERT INTO pricing_rules (
            provider_model, billing_mode, token_rate, rounding_mode,
            precharge_default_units, active_from
        ) VALUES (
            :provider_model,
            CAST(:billing_mode AS pricing_billing_mode),
            :token_rate,
            CAST(:rounding_mode AS rounding_mode),
            :precharge_default_units,
            :active_from
        )
        """
    )


async def test_resolve_active_rule_picks_latest_active_from(
    engine, pricing_service
):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with engine.begin() as conn:
        for active_from, rate in (
            (base, "1"),
            (base + timedelta(days=30), "2"),
            (base + timedelta(days=60), "3"),
        ):
            await conn.execute(
                _insert_rule_sql(),
                {
                    "provider_model": "fal-ai/x",
                    "billing_mode": "per_track",
                    "token_rate": rate,
                    "rounding_mode": "ceil",
                    "precharge_default_units": None,
                    "active_from": active_from,
                },
            )
    # at "+90 days" — самая свежая
    rule = await pricing_service.resolve_active_rule(
        provider_model="fal-ai/x", at=base + timedelta(days=90)
    )
    assert rule.token_rate == Decimal("3.0000")
    # at "+45 days" — средняя
    rule = await pricing_service.resolve_active_rule(
        provider_model="fal-ai/x", at=base + timedelta(days=45)
    )
    assert rule.token_rate == Decimal("2.0000")


async def test_resolve_missing_raises(pricing_service):
    with pytest.raises(PricingRuleMissing):
        await pricing_service.resolve_active_rule(provider_model="nope")


def _rule(
    mode: BillingMode,
    rate: str,
    rounding: RoundingMode = RoundingMode.ceil,
    precharge_default: str | None = None,
) -> PricingRule:
    return PricingRule(
        provider_model="fal-ai/x",
        billing_mode=mode,
        token_rate=Decimal(rate),
        rounding_mode=rounding,
        precharge_default_units=(
            Decimal(precharge_default) if precharge_default else None
        ),
        active_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_per_track_returns_token_rate_rounded():
    assert (
        PricingService.required_tokens_for_precharge(
            _rule(BillingMode.per_track, "1"), requested_duration_seconds=None
        )
        == 1
    )
    assert (
        PricingService.required_tokens_for_precharge(
            _rule(BillingMode.per_track, "2.4"), requested_duration_seconds=None
        )
        == 3  # ceil
    )


def test_per_minute_uses_requested_duration():
    rule = _rule(BillingMode.per_minute, "1")
    # 90s → 1.5 min → ceil(1.5) = 2
    assert (
        PricingService.required_tokens_for_precharge(
            rule, requested_duration_seconds=90
        )
        == 2
    )


def test_per_minute_falls_back_to_default_units():
    rule = _rule(BillingMode.per_minute, "1", precharge_default="3")
    assert (
        PricingService.required_tokens_for_precharge(
            rule, requested_duration_seconds=None
        )
        == 3
    )


def test_floor_and_nearest_rounding_modes():
    floor_rule = _rule(BillingMode.per_minute, "1", RoundingMode.floor)
    nearest_rule = _rule(BillingMode.per_minute, "1", RoundingMode.nearest)
    # 90s = 1.5 min
    assert (
        PricingService.required_tokens_for_precharge(
            floor_rule, requested_duration_seconds=90
        )
        == 1  # actually 1.5 -> floor=1, but max(1, ...) keeps it 1
    )
    assert (
        PricingService.required_tokens_for_precharge(
            nearest_rule, requested_duration_seconds=90
        )
        == 2  # round-half-up
    )


def test_capture_per_minute_uses_actual_duration():
    rule = _rule(BillingMode.per_minute, "1")
    # 35s → 0.583 min → ceil = 1
    assert (
        PricingService.required_tokens_for_capture(
            rule, actual_duration_seconds=35
        )
        == 1
    )
    # 125s → 2.08 min → ceil = 3
    assert (
        PricingService.required_tokens_for_capture(
            rule, actual_duration_seconds=125
        )
        == 3
    )
