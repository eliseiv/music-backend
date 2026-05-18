"""PricingService — выбор активного тарифа и расчёт стоимости в токенах."""
from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import PricingRuleMissing
from app.music.enums import BillingMode, RoundingMode
from app.music.models import PricingRule
from app.music.repositories.pricing import PricingRulesRepository


def _round(value: Decimal, mode: RoundingMode) -> int:
    if mode is RoundingMode.ceil:
        return math.ceil(value)
    if mode is RoundingMode.floor:
        return math.floor(value)
    # nearest (round-half-up; Python's banker's rounding is undesirable here)
    return int(value + Decimal("0.5"))


def _minutes_from_seconds(seconds: float | int | Decimal) -> Decimal:
    return Decimal(seconds) / Decimal(60)


class PricingService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def resolve_active_rule(
        self,
        *,
        provider_model: str,
        at: datetime | None = None,
    ) -> PricingRule:
        async with self._sessionmaker() as session:
            repo = PricingRulesRepository(session)
            rule = await repo.get_active_rule(
                provider_model=provider_model, at=at
            )
            if rule is None:
                raise PricingRuleMissing(
                    details={"provider_model": provider_model}
                )
            return rule

    @staticmethod
    def required_tokens_for_precharge(
        rule: PricingRule,
        *,
        requested_duration_seconds: float | int | None,
    ) -> int:
        if rule.billing_mode is BillingMode.per_track:
            return max(1, _round(rule.token_rate, rule.rounding_mode))
        # per_minute
        if requested_duration_seconds is not None:
            minutes = _minutes_from_seconds(requested_duration_seconds)
        elif rule.precharge_default_units is not None:
            minutes = rule.precharge_default_units
        else:
            minutes = Decimal("1")
        return max(1, _round(rule.token_rate * minutes, rule.rounding_mode))

    @staticmethod
    def required_tokens_for_capture(
        rule: PricingRule,
        *,
        actual_duration_seconds: float | int,
    ) -> int:
        if rule.billing_mode is BillingMode.per_track:
            return max(1, _round(rule.token_rate, rule.rounding_mode))
        minutes = _minutes_from_seconds(actual_duration_seconds)
        return max(1, _round(rule.token_rate * minutes, rule.rounding_mode))
