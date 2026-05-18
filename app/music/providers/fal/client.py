from __future__ import annotations

import json
import logging
from typing import Any, Mapping

import httpx

from app.api.errors import FalProviderError, FalTimeout, WebhookPayloadInvalid
from app.logging_config import provider_var
from app.music.providers.fal.base import FalSubmitResult, FalWebhookEvent
from app.music.providers.fal.signature import body_digest, verify_signature

logger = logging.getLogger(__name__)


class FalAiProvider:
    """Async client for fal.ai queue API + storage uploads."""

    PROVIDER_NAME = "fal"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        music_model: str,
        refine_model: str,
        speech_model: str,
        webhook_secret: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("FAL_API_KEY is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._music_model = music_model
        self._refine_model = refine_model
        self._speech_model = speech_model
        self._webhook_secret = webhook_secret
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Authorization": f"Key {api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- public submit methods ----------

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
        payload: dict[str, Any] = {"prompt": prompt}
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
        if lyrics:
            payload["lyrics"] = lyrics
        if reference_audio_url:
            payload["reference_audio_url"] = reference_audio_url
        return await self._submit(
            model=self._music_model,
            payload=payload,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
        )

    async def submit_audio_to_audio_refine(
        self,
        *,
        source_audio_url: str,
        prompt: str,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        return await self._submit(
            model=self._refine_model,
            payload={"audio_url": source_audio_url, "prompt": prompt},
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
        )

    async def submit_speech(
        self,
        *,
        text: str,
        voice: str | None,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        payload: dict[str, Any] = {"text": text}
        if voice:
            payload["voice"] = voice
        return await self._submit(
            model=self._speech_model,
            payload=payload,
            webhook_url=webhook_url,
            idempotency_key=idempotency_key,
        )

    async def upload_to_storage(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        url = f"{self._base_url}/storage/upload"
        token = provider_var.set(self.PROVIDER_NAME)
        try:
            files = {"file": (filename, content, content_type)}
            try:
                response = await self._client.post(url, files=files)
            except httpx.TimeoutException as exc:
                raise FalTimeout() from exc
            except httpx.HTTPError as exc:
                raise FalProviderError(
                    f"fal storage upload failed: {exc.__class__.__name__}"
                ) from exc
            if response.status_code >= 500:
                raise FalProviderError(
                    f"fal storage returned {response.status_code}"
                )
            if response.status_code >= 400:
                raise FalProviderError(
                    f"fal storage rejected upload ({response.status_code}): "
                    f"{response.text[:200]}"
                )
            data = response.json()
            url_value = data.get("url") or data.get("file_url") or data.get(
                "uploaded_url"
            )
            if not url_value:
                raise FalProviderError(
                    "fal storage response missing url field"
                )
            return url_value
        finally:
            provider_var.reset(token)

    def parse_webhook_event(
        self, *, headers: Mapping[str, str], raw_body: bytes
    ) -> FalWebhookEvent:
        verify_signature(
            secret=self._webhook_secret, raw_body=raw_body, headers=headers
        )
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookPayloadInvalid(
                details={"reason": "not_json"}
            ) from exc
        if not isinstance(data, dict):
            raise WebhookPayloadInvalid(details={"reason": "not_object"})

        request_id = (
            data.get("request_id")
            or data.get("requestId")
            or data.get("id")
        )
        if not request_id:
            raise WebhookPayloadInvalid(details={"reason": "no_request_id"})

        status = (data.get("status") or "").lower()
        if status not in {"completed", "failed", "canceled", "in_progress"}:
            # Some fal events use 'OK'/'COMPLETED' — normalize.
            if status in {"ok", "success"}:
                status = "completed"
            else:
                raise WebhookPayloadInvalid(
                    details={"reason": "unknown_status", "status": status}
                )

        result = data.get("result") or data.get("output") or {}
        if not isinstance(result, dict):
            result = {}
        audio_url = result.get("audio_url") or result.get("audio", {}).get(
            "url"
        ) if isinstance(result.get("audio"), dict) else result.get("audio_url")
        duration_seconds = result.get("duration_seconds") or result.get(
            "duration"
        )
        stems = result.get("stems") if isinstance(result.get("stems"), dict) else None
        error_message = data.get("error") or data.get("error_message")

        event_id = (
            data.get("event_id")
            or data.get("eventId")
            or f"{request_id}:{status}"
        )

        return FalWebhookEvent(
            request_id=str(request_id),
            status=status,
            audio_url=audio_url,
            duration_seconds=(
                float(duration_seconds) if duration_seconds is not None else None
            ),
            stems=stems,
            error_message=str(error_message) if error_message else None,
            raw=data,
            event_id=str(event_id),
            payload_digest=body_digest(raw_body),
        )

    # ---------- private ----------

    async def _submit(
        self,
        *,
        model: str,
        payload: dict[str, Any],
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        url = f"{self._base_url}/{model}"
        params = {}
        headers: dict[str, str] = {"X-Idempotency-Key": idempotency_key}
        if webhook_url:
            params["fal_webhook"] = webhook_url
        token = provider_var.set(self.PROVIDER_NAME)
        try:
            try:
                response = await self._client.post(
                    url, json=payload, params=params, headers=headers
                )
            except httpx.TimeoutException as exc:
                raise FalTimeout() from exc
            except httpx.HTTPError as exc:
                raise FalProviderError(
                    f"fal submit failed: {exc.__class__.__name__}: {exc}"
                ) from exc
            if response.status_code >= 500:
                raise FalProviderError(
                    f"fal returned {response.status_code} for {model}"
                )
            if response.status_code >= 400:
                raise FalProviderError(
                    f"fal rejected submit ({response.status_code}): "
                    f"{response.text[:200]}"
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise FalProviderError("fal returned non-JSON body") from exc
            request_id = (
                data.get("request_id")
                or data.get("requestId")
                or data.get("id")
            )
            if not request_id:
                raise FalProviderError("fal response missing request_id")
            return FalSubmitResult(
                request_id=str(request_id),
                status=(data.get("status") or "queued").lower(),
                audio_url=data.get("audio_url"),
                duration_seconds=data.get("duration_seconds"),
                raw=data,
            )
        finally:
            provider_var.reset(token)


