from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.enums import WebhookProvider
from app.music.models import ProcessedWebhook


class WebhooksRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_record(
        self,
        *,
        provider: WebhookProvider,
        event_id: str,
        payload_digest: str,
        outcome: str = "received",
        meta: dict[str, Any] | None = None,
    ) -> bool:

        stmt = (
            pg_insert(ProcessedWebhook)
            .values(
                provider=provider,
                event_id=event_id,
                payload_digest=payload_digest,
                outcome=outcome,
                meta=meta,
            )
            .on_conflict_do_nothing(index_elements=["provider", "event_id"])
            .returning(ProcessedWebhook.event_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_applied(
        self,
        *,
        provider: WebhookProvider,
        event_id: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {"outcome": "applied"}
        if meta is not None:
            values["meta"] = meta
        await self._session.execute(
            update(ProcessedWebhook)
            .where(
                ProcessedWebhook.provider == provider,
                ProcessedWebhook.event_id == event_id,
            )
            .values(**values)
        )

    async def list_received(
        self, *, provider: WebhookProvider | None = None, limit: int = 100
    ) -> list[ProcessedWebhook]:
        """События в статусе `received`, которые не успели примениться."""
        stmt = select(ProcessedWebhook).where(
            ProcessedWebhook.outcome == "received"
        )
        if provider is not None:
            stmt = stmt.where(ProcessedWebhook.provider == provider)
        stmt = stmt.order_by(ProcessedWebhook.received_at).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())
