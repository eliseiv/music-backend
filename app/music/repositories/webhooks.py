from __future__ import annotations

from typing import Any

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
        outcome: str = "applied",
        meta: dict[str, Any] | None = None,
    ) -> bool:
        """INSERT ON CONFLICT DO NOTHING. Returns True if new row, False if duplicate."""
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
