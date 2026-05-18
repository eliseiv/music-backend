from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import httpx

from app.api.errors import InvalidSampleUrl

logger = logging.getLogger(__name__)


async def _check_one(client: httpx.AsyncClient, url: str) -> tuple[str, str | None]:
    """Возвращает (url, error_reason_or_None)."""
    try:
        resp = await client.head(url, follow_redirects=True)
        if resp.status_code == 405:
            # fallback: некоторые CDN не поддерживают HEAD
            resp = await client.get(
                url,
                follow_redirects=True,
                headers={"Range": "bytes=0-0"},
            )
        if 200 <= resp.status_code < 400:
            return url, None
        return url, f"http_{resp.status_code}"
    except httpx.TimeoutException:
        return url, "timeout"
    except httpx.HTTPError as exc:
        return url, f"http_error:{exc.__class__.__name__}"


async def validate_urls_reachable(
    urls: Iterable[str],
    *,
    timeout_seconds: float = 3.0,
    enabled: bool = True,
) -> None:
    """Если enabled=False — no-op (для dev/test).

    Дубликаты URL дедуплицируются. При первой неудаче бросает InvalidSampleUrl.
    """
    if not enabled:
        return
    unique = list({u for u in urls if u})
    if not unique:
        return
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": "music-backend/url-validator"},
    ) as client:
        results = await asyncio.gather(
            *(_check_one(client, u) for u in unique),
            return_exceptions=False,
        )
    bad = [(u, r) for u, r in results if r is not None]
    if bad:
        url, reason = bad[0]
        logger.info("URL validation failed for %s: %s", url, reason)
        raise InvalidSampleUrl(
            details={"url": url, "reason": reason, "failed_count": len(bad)}
        )
