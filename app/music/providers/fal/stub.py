"""In-process stub for FalProvider — для dev/смоук-тестов без реального fal.ai.

Активируется флагом `FAL_USE_STUB=true` в `.env`. Возвращает синтетические
ответы и подписывает webhook'и тем же `FAL_WEBHOOK_SECRET`.
"""
from __future__ import annotations

import logging
import uuid
from typing import Mapping

from app.music.providers.fal.base import FalStatusResult, FalSubmitResult, FalWebhookEvent
from app.music.providers.fal.signature import body_digest, verify_signature

logger = logging.getLogger(__name__)


class StubFalProvider:
    PROVIDER_NAME = "fal-stub"

    def __init__(self, *, webhook_secret: str = "") -> None:
        self._webhook_secret = webhook_secret

    async def aclose(self) -> None:
        pass

    async def submit_music_generation(
        self,
        *,
        prompt: str,
        duration_seconds: float | None,
        lyrics: str | None,
        reference_audio_url: str | None,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        request_id = f"stub-music-{uuid.uuid4().hex[:8]}"
        logger.info(
            "StubFal: submit_music_generation prompt=%.60s request_id=%s",
            prompt,
            request_id,
        )
        return FalSubmitResult(
            request_id=request_id,
            status="queued",
            audio_url=None,
            duration_seconds=duration_seconds,
            raw={"stub": True, "model": "music"},
        )

    async def submit_audio_to_audio_refine(
        self,
        *,
        source_audio_url: str,
        prompt: str,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        request_id = f"stub-refine-{uuid.uuid4().hex[:8]}"
        return FalSubmitResult(
            request_id=request_id, status="queued", raw={"stub": True}
        )

    async def submit_speech(
        self,
        *,
        text: str,
        voice: str | None,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        request_id = f"stub-speech-{uuid.uuid4().hex[:8]}"
        return FalSubmitResult(
            request_id=request_id, status="queued", raw={"stub": True}
        )

    async def upload_to_storage(
        self, *, content: bytes, filename: str, content_type: str
    ) -> str:
        return f"https://fal-stub-cdn.local/{uuid.uuid4().hex}/{filename}"

    async def fetch_status(
        self, *, model: str, request_id: str
    ) -> FalStatusResult:
        # Stub всегда говорит IN_QUEUE — для unit-тестов мы используем
        # emit_webhook, polling не нужен.
        return FalStatusResult(
            request_id=request_id, status="IN_QUEUE", raw={"stub": True}
        )

    def parse_webhook_event(
        self, *, headers: Mapping[str, str], raw_body: bytes
    ) -> FalWebhookEvent:
        # Stub использует ту же HMAC-логику, что и real client.
        verify_signature(
            secret=self._webhook_secret, raw_body=raw_body, headers=headers
        )
        import json

        from app.api.errors import WebhookPayloadInvalid

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise WebhookPayloadInvalid(details={"reason": "not_json"}) from exc
        request_id = data.get("request_id") or data.get("id")
        if not request_id:
            raise WebhookPayloadInvalid(details={"reason": "no_request_id"})
        status_value = (data.get("status") or "completed").lower()
        result = data.get("result") or {}
        return FalWebhookEvent(
            request_id=str(request_id),
            status=status_value,
            audio_url=result.get("audio_url"),
            duration_seconds=result.get("duration_seconds"),
            stems=result.get("stems") if isinstance(result.get("stems"), dict) else None,
            error_message=data.get("error"),
            raw=data,
            event_id=str(data.get("event_id") or f"{request_id}:{status_value}"),
            payload_digest=body_digest(raw_body),
        )
