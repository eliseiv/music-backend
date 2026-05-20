"""Парсит длительность audio-файла из его содержимого (через mutagen).

Используется когда fal не возвращает duration_seconds в JSON-ответе
(например, для stable-audio: только `audio_file.url` без duration).

Стратегия:
1. Range-запрос на первые ~512 KB файла (для mp3/wav этого хватает на ID3/header).
2. mutagen.File(BytesIO) → info.length (seconds, float).
3. Если range-запрос не вернул хватает — фолбэк на полный download (≤25 MB).
"""
from __future__ import annotations

import io
import logging

import httpx

logger = logging.getLogger(__name__)

PARTIAL_FETCH_BYTES = 512 * 1024  # 512 KB
FULL_FETCH_LIMIT = 25 * 1024 * 1024  # 25 MB hard cap


async def probe_duration_seconds(url: str, *, timeout: float = 30.0) -> float | None:
    """Возвращает длительность аудио в секундах или None при ошибке.

    Не бросает исключения — все ошибки логирует и возвращает None.
    """
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            # 1) Сначала Range — экономим трафик
            duration = await _probe_partial(client, url)
            if duration is not None and duration > 0:
                return duration
            # 2) Fallback — полный файл (с лимитом)
            return await _probe_full(client, url)
    except Exception as e:
        logger.warning("probe_duration_seconds(%s) failed: %s", url, e)
        return None


async def _probe_partial(client: httpx.AsyncClient, url: str) -> float | None:
    try:
        resp = await client.get(
            url, headers={"Range": f"bytes=0-{PARTIAL_FETCH_BYTES - 1}"}
        )
    except httpx.HTTPError:
        return None
    if resp.status_code not in (200, 206):
        return None
    return _parse(resp.content)


async def _probe_full(client: httpx.AsyncClient, url: str) -> float | None:
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    if len(resp.content) > FULL_FETCH_LIMIT:
        logger.warning("audio too large (%d bytes), skipping probe", len(resp.content))
        return None
    return _parse(resp.content)


def _parse(content: bytes) -> float | None:
    """Распарсить через mutagen. None при ошибке."""
    if not content:
        return None
    try:
        from mutagen import File as MutagenFile  # lazy import
    except ImportError:
        logger.warning("mutagen not installed — duration probe disabled")
        return None
    try:
        f = MutagenFile(io.BytesIO(content))
        if f is None or getattr(f, "info", None) is None:
            return None
        length = float(f.info.length or 0)
        if length <= 0:
            return None
        return length
    except Exception as e:
        logger.debug("mutagen parse failed: %s", e)
        return None
