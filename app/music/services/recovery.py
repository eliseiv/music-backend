"""Recovery sweep on startup.

1. Orphan jobs — jobs в queued/processing без provider_request_id (т.е. упали
   до или сразу после submit). Освобождаем токены и помечаем failed.

2. Received-but-not-applied webhooks (ТЗ §14.1 — 2-фазная обработка):
   если процесс упал между `try_record(outcome="received")` и
   `mark_applied`, событие останется в outcome="received". Логируем для
   ручного разбора (автоматическое переприменение сложное, потому что raw
   payload не сохраняется — нужно либо хранить payload в processed_webhooks,
   либо алертить оператору).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.music.enums import WebhookProvider
from app.music.repositories.jobs import JobsRepository
from app.music.repositories.webhooks import WebhooksRepository
from app.music.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


async def recover_orphan_jobs(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    wallet: WalletService,
) -> int:
    """Mark queued-without-provider-request-id jobs as failed and refund tokens."""
    async with sessionmaker() as session:
        repo = JobsRepository(session)
        orphans = await repo.list_orphans()
    if not orphans:
        return 0
    for job in orphans:
        try:
            await wallet.release(
                user_id=job.user_id,
                amount=job.reserved_tokens,
                ref_type="job",
                ref_id=str(job.id),
            )
            async with sessionmaker() as session:
                async with session.begin():
                    inner = JobsRepository(session)
                    await inner.mark_failed(
                        job_id=job.id,
                        error_code="STARTUP_RECOVERY",
                        error_message="job was queued without provider_request_id",
                    )
            logger.info("Recovered orphan job %s", job.id)
        except Exception:
            logger.exception("Failed to recover orphan job %s", job.id)
    return len(orphans)


async def report_received_webhooks(
    *, sessionmaker: async_sessionmaker[AsyncSession]
) -> int:
    """Find webhooks stuck in outcome='received' and log for operator review.

    Возвращает количество найденных. Не делает автоматическую переобработку,
    так как raw payload не сохранён.
    """
    async with sessionmaker() as session:
        repo = WebhooksRepository(session)
        stuck = await repo.list_received(limit=500)
    if not stuck:
        return 0
    for w in stuck:
        logger.warning(
            "Webhook stuck in 'received': provider=%s event_id=%s received_at=%s",
            w.provider.value if hasattr(w.provider, "value") else w.provider,
            w.event_id,
            w.received_at,
        )
    return len(stuck)
