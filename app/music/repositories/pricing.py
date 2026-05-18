from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.models import PricingRule


class PricingRulesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_rule(
        self,
        *,
        provider_model: str,
        at: datetime | None = None,
    ) -> PricingRule | None:
        at = at or datetime.now(tz=timezone.utc)
        stmt = (
            select(PricingRule)
            .where(
                PricingRule.provider_model == provider_model,
                PricingRule.active_from <= at,
            )
            .order_by(PricingRule.active_from.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
