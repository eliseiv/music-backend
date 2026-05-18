from __future__ import annotations

import json
from collections import deque
from typing import Any, Mapping

from app.api.errors import WebhookPayloadInvalid, WebhookSignatureInvalid
from app.music.providers.fal.base import FalSubmitResult, FalWebhookEvent
from app.music.providers.fal.signature import body_digest


class FakeFal:
    """Test double for `FalAiProvider`."""

    PROVIDER_NAME = "fal"

    def __init__(self) -> None:
        # Queued canned responses per method
        self.music_results: deque = deque()
        self.refine_results: deque = deque()
        self.speech_results: deque = deque()
        self.uploaded_url: str = "https://fake-fal-cdn/uploaded.wav"
        # Calls recorded for assertions
        self.calls_music: list[dict[str, Any]] = []
        self.calls_refine: list[dict[str, Any]] = []
        self.calls_speech: list[dict[str, Any]] = []
        self.calls_upload: list[dict[str, Any]] = []
        # Webhook fixture
        self._webhook_events: deque = deque()
        self._accept_any_signature = True
        self.webhook_secret = "test-secret"

    # --- queueing helpers used by tests ---

    def queue_music_result(self, **kwargs) -> None:
        self.music_results.append(FalSubmitResult(**kwargs))

    def queue_speech_result(self, **kwargs) -> None:
        self.speech_results.append(FalSubmitResult(**kwargs))

    def queue_refine_result(self, **kwargs) -> None:
        self.refine_results.append(FalSubmitResult(**kwargs))

    def queue_webhook_event(self, event: FalWebhookEvent) -> None:
        self._webhook_events.append(event)

    # --- provider protocol ---

    async def submit_music_generation(self, **kwargs) -> FalSubmitResult:
        self.calls_music.append(kwargs)
        if not self.music_results:
            return FalSubmitResult(
                request_id=f"fake-music-{len(self.calls_music)}",
                status="queued",
            )
        return self.music_results.popleft()

    async def submit_audio_to_audio_refine(self, **kwargs) -> FalSubmitResult:
        self.calls_refine.append(kwargs)
        if not self.refine_results:
            return FalSubmitResult(
                request_id=f"fake-refine-{len(self.calls_refine)}",
                status="queued",
            )
        return self.refine_results.popleft()

    async def submit_speech(self, **kwargs) -> FalSubmitResult:
        self.calls_speech.append(kwargs)
        if not self.speech_results:
            return FalSubmitResult(
                request_id=f"fake-speech-{len(self.calls_speech)}",
                status="queued",
            )
        return self.speech_results.popleft()

    async def upload_to_storage(
        self, *, content: bytes, filename: str, content_type: str
    ) -> str:
        self.calls_upload.append(
            {
                "filename": filename,
                "content_type": content_type,
                "size": len(content),
            }
        )
        return self.uploaded_url

    def parse_webhook_event(
        self, *, headers: Mapping[str, str], raw_body: bytes
    ) -> FalWebhookEvent:
        if self._webhook_events:
            event = self._webhook_events.popleft()
            return event
        # Fallback: parse JSON directly, no signature check.
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise WebhookPayloadInvalid(details={"reason": "not_json"}) from exc
        request_id = data.get("request_id") or data.get("id")
        if not request_id:
            raise WebhookPayloadInvalid(details={"reason": "no_request_id"})
        status = (data.get("status") or "completed").lower()
        result = data.get("result") or {}
        return FalWebhookEvent(
            request_id=str(request_id),
            status=status,
            audio_url=result.get("audio_url"),
            duration_seconds=result.get("duration_seconds"),
            stems=result.get("stems") if isinstance(result.get("stems"), dict) else None,
            error_message=data.get("error"),
            raw=data,
            event_id=data.get("event_id") or f"{request_id}:{status}",
            payload_digest=body_digest(raw_body),
        )

    async def aclose(self) -> None:
        pass
