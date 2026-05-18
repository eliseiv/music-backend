from __future__ import annotations

import json
import logging
from typing import Any, Mapping

import httpx

from app.api.errors import FalProviderError, FalTimeout, WebhookPayloadInvalid
from app.logging_config import provider_var
from app.music.providers.fal.base import FalStatusResult, FalSubmitResult, FalWebhookEvent
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

    # fal storage API живёт на rest.alpha.fal.ai (не queue.fal.run).
    # Upload — two-step: initiate (получаем presigned URL) → PUT с файлом.
    REST_STORAGE_BASE = "https://rest.alpha.fal.ai"

    async def upload_to_storage(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        token = provider_var.set(self.PROVIDER_NAME)
        try:
            # 1. initiate — получаем presigned upload_url + финальный file_url
            initiate_url = f"{self.REST_STORAGE_BASE}/storage/upload/initiate"
            try:
                init_resp = await self._client.post(
                    initiate_url,
                    json={"file_name": filename, "content_type": content_type},
                )
            except httpx.TimeoutException as exc:
                raise FalTimeout() from exc
            except httpx.HTTPError as exc:
                raise FalProviderError(
                    f"fal storage initiate failed: {exc.__class__.__name__}"
                ) from exc
            if init_resp.status_code >= 400:
                raise FalProviderError(
                    f"fal storage initiate rejected ({init_resp.status_code}): "
                    f"{init_resp.text[:200]}"
                )
            init_data = init_resp.json()
            upload_url = init_data.get("upload_url")
            file_url = init_data.get("file_url")
            if not upload_url or not file_url:
                raise FalProviderError(
                    "fal storage initiate response missing upload_url/file_url"
                )

            # 2. PUT — загружаем файл по presigned URL. Authorization не нужен —
            # presigned URL уже содержит подпись.
            try:
                put_resp = await self._client.put(
                    upload_url,
                    content=content,
                    headers={"Content-Type": content_type},
                    # presigned URL не любит наш дефолтный Authorization
                )
            except httpx.TimeoutException as exc:
                raise FalTimeout() from exc
            except httpx.HTTPError as exc:
                raise FalProviderError(
                    f"fal storage PUT failed: {exc.__class__.__name__}"
                ) from exc
            if put_resp.status_code >= 400:
                raise FalProviderError(
                    f"fal storage PUT rejected ({put_resp.status_code}): "
                    f"{put_resp.text[:200]}"
                )
            return file_url
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

    async def fetch_status(
        self, *, model: str, request_id: str
    ) -> FalStatusResult:
        """Polling fal queue API: GET /{model}/requests/{rid}/status.

        Если COMPLETED — дополнительно делает GET /{model}/requests/{rid}
        для получения результата (audio_url, duration_seconds, stems).
        """
        token = provider_var.set(self.PROVIDER_NAME)
        try:
            status_url = (
                f"{self._base_url}/{model}/requests/{request_id}/status"
            )
            try:
                resp = await self._client.get(status_url)
            except httpx.TimeoutException as exc:
                raise FalTimeout() from exc
            except httpx.HTTPError as exc:
                raise FalProviderError(
                    f"fal status fetch failed: {exc.__class__.__name__}"
                ) from exc
            if resp.status_code == 404:
                # Job ещё не виден в очереди или истёк TTL
                return FalStatusResult(
                    request_id=request_id, status="IN_QUEUE", raw={}
                )
            if resp.status_code >= 400:
                raise FalProviderError(
                    f"fal status returned {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            status = str(data.get("status") or "").upper()

            if status != "COMPLETED":
                return FalStatusResult(
                    request_id=request_id,
                    status=status or "IN_QUEUE",
                    raw=data,
                )

            # Забираем результат
            result_url = f"{self._base_url}/{model}/requests/{request_id}"
            try:
                result_resp = await self._client.get(result_url)
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                raise FalProviderError(
                    f"fal result fetch failed: {exc.__class__.__name__}"
                ) from exc
            if result_resp.status_code == 422:
                # fal приняла задачу в очередь, но при обработке отбросила
                # её как невалидную — это финальный failed.
                detail = result_resp.text[:300]
                return FalStatusResult(
                    request_id=request_id,
                    status="FAILED",
                    error_message=f"422 Unprocessable: {detail}",
                    raw={"status_code": 422, "body": detail},
                )
            if result_resp.status_code >= 400:
                return FalStatusResult(
                    request_id=request_id,
                    status="FAILED",
                    error_message=f"result {result_resp.status_code}: {result_resp.text[:200]}",
                    raw={},
                )
            result_data = result_resp.json()
            # Достаём audio_url из разных вариантов формата ответа
            audio_url = None
            duration = None
            stems = None
            for key in ("audio", "audio_url", "output", "result"):
                v = result_data.get(key)
                if isinstance(v, dict):
                    audio_url = audio_url or v.get("url") or v.get("audio_url")
                    duration = duration or v.get("duration") or v.get("duration_seconds")
                elif isinstance(v, str):
                    audio_url = audio_url or v
            audio_url = audio_url or result_data.get("audio_url")
            duration = duration or result_data.get("duration_seconds")
            stems_field = result_data.get("stems")
            if isinstance(stems_field, dict):
                stems = stems_field

            return FalStatusResult(
                request_id=request_id,
                status="COMPLETED",
                audio_url=audio_url,
                duration_seconds=float(duration) if duration is not None else None,
                stems=stems,
                raw=result_data,
            )
        finally:
            provider_var.reset(token)
