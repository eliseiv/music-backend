"""Recovery sweep on startup — освобождаем токены под orphan-jobs."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.music.repositories.jobs import JobsRepository
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
                        error_code="startup_recovery",
                        error_message="job was queued without provider_request_id",
                    )
            logger.info("Recovered orphan job %s", job.id)
        except Exception:
            logger.exception("Failed to recover orphan job %s", job.id)
    return len(orphans)
