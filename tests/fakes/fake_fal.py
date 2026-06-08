from __future__ import annotations

import hashlib
import hmac
import json
from collections import deque
from typing import Any, Mapping

from app.api.errors import WebhookPayloadInvalid, WebhookSignatureInvalid
from app.music.providers.fal.base import FalStatusResult, FalSubmitResult, FalWebhookEvent
from app.music.providers.fal.signature import SIGNATURE_HEADER, body_digest


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

    async def submit_stable_audio(self, **kwargs) -> FalSubmitResult:
        if not hasattr(self, "calls_stable"):
            self.calls_stable = []
        self.calls_stable.append(kwargs)
        return FalSubmitResult(
            request_id=f"fake-stable-{len(self.calls_stable)}",
            status="queued",
        )

    async def generate_lyrics(self, **kwargs) -> str:
        if not hasattr(self, "calls_lyrics"):
            self.calls_lyrics = []
        self.calls_lyrics.append(kwargs)
        prompt = kwargs.get("prompt", "")
        return f"Fake lyrics about {prompt[:30]}\nLine 2\nLine 3"

    async def submit_ace_step_vocal(self, **kwargs) -> FalSubmitResult:
        if not hasattr(self, "calls_acestep"):
            self.calls_acestep = []
        self.calls_acestep.append(kwargs)
        return FalSubmitResult(
            request_id=f"fake-acestep-{len(self.calls_acestep)}",
            status="queued",
        )

    async def submit_speech(self, **kwargs) -> FalSubmitResult:
        self.calls_speech.append(kwargs)
        if not self.speech_results:
            return FalSubmitResult(
                request_id=f"fake-speech-{len(self.calls_speech)}",
                status="queued",
            )
        return self.speech_results.popleft()

    async def voice_clone(self, *, audio_url: str) -> str:
        if not hasattr(self, "calls_voice_clone"):
            self.calls_voice_clone = []
        self.calls_voice_clone.append({"audio_url": audio_url})
        if getattr(self, "voice_clone_should_fail", False):
            from app.api.errors import FalProviderError

            raise FalProviderError(
                'voice_clone returned 422: {"detail":[{"msg":"No valid audio clips found"}]}'
            )
        return f"fake-cloned-voice-{len(self.calls_voice_clone)}"

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
        # Real fal-like path: verify HMAC signature.
        from app.music.providers.fal.signature import verify_signature

        verify_signature(
            secret=self.webhook_secret,
            raw_body=raw_body,
            headers=headers,
        )
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

    async def fetch_status(
        self, *, model: str, request_id: str
    ) -> FalStatusResult:
        # Тесты используют emit_webhook, поллинг по-умолчанию говорит IN_QUEUE
        return FalStatusResult(request_id=request_id, status="IN_QUEUE", raw={})

    # --- test helpers for end-to-end pipeline tests ---

    def build_webhook_payload(
        self,
        *,
        request_id: str,
        status: str = "completed",
        audio_url: str | None = None,
        duration_seconds: float | None = None,
        stems: dict[str, Any] | None = None,
        error: str | None = None,
        event_id: str | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        """Build raw body + headers (with valid HMAC signature) for POST /v1/webhooks/fal."""
        body: dict[str, Any] = {"request_id": request_id, "status": status}
        if event_id is not None:
            body["event_id"] = event_id
        result: dict[str, Any] = {}
        if audio_url is not None:
            result["audio_url"] = audio_url
        if duration_seconds is not None:
            result["duration_seconds"] = duration_seconds
        if stems is not None:
            result["stems"] = stems
        if result:
            body["result"] = result
        if error is not None:
            body["error"] = error
        raw = json.dumps(body).encode("utf-8")
        sig = hmac.new(
            self.webhook_secret.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        return raw, {
            SIGNATURE_HEADER: sig,
            "Content-Type": "application/json",
        }

    async def emit_webhook(
        self,
        client,
        *,
        request_id: str,
        status: str = "completed",
        audio_url: str | None = None,
        duration_seconds: float | None = None,
        stems: dict[str, Any] | None = None,
        error: str | None = None,
        event_id: str | None = None,
    ):
        """POST a signed webhook to /v1/webhooks/fal via an AsyncClient."""
        raw, headers = self.build_webhook_payload(
            request_id=request_id,
            status=status,
            audio_url=audio_url,
            duration_seconds=duration_seconds,
            stems=stems,
            error=error,
            event_id=event_id,
        )
        return await client.post(
            "/v1/webhooks/fal", content=raw, headers=headers
        )
