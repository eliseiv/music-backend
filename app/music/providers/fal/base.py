from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass
class FalSubmitResult:
    request_id: str
    status: str  # 'queued' | 'in_progress' | 'completed'
    audio_url: str | None = None
    duration_seconds: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FalStatusResult:
    """Результат опроса fal queue API (GET /requests/{rid}/status и /requests/{rid}).

    `status` — `IN_QUEUE` | `IN_PROGRESS` | `COMPLETED` | `FAILED` | `CANCELED`.
    """

    request_id: str
    status: str
    audio_url: str | None = None
    duration_seconds: float | None = None
    stems: dict[str, Any] | None = None
    error_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FalWebhookEvent:
    """Нормализованное событие webhook от fal.

    `status` — финальный статус ('completed' | 'failed' | 'canceled').
    `request_id` — provider_request_id, который мы сохранили при submit.
    """

    request_id: str
    status: str
    audio_url: str | None
    duration_seconds: float | None
    stems: dict[str, Any] | None
    error_message: str | None
    raw: dict[str, Any]
    event_id: str
    payload_digest: str


class FalProvider(Protocol):
    PROVIDER_NAME: str

    async def submit_music_generation(
        self,
        *,
        prompt: str,
        duration_seconds: float | None,
        lyrics: str | None,
        reference_audio_url: str | None,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult: ...

    async def submit_audio_to_audio_refine(
        self,
        *,
        source_audio_url: str,
        prompt: str,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult: ...

    async def submit_stable_audio(
        self,
        *,
        prompt: str,
        seconds_total: int,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        """Fallback music generator (fal-ai/stable-audio).

        Используется когда minimax-music даёт PROVIDER_FAILED — у fal часто
        отваливается их upstream к MiniMax.
        """
        ...

    async def generate_lyrics(
        self,
        *,
        prompt: str,
        language: str = "en",
    ) -> str:
        """Превращает пользовательский тема-промт в готовый текст песни через
        LLM (fal-ai/any-llm). Возвращает чистый текст без preamble/markdown.
        """
        ...

    async def submit_ace_step_vocal(
        self,
        *,
        tags: str,
        lyrics: str,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult:
        """Vocal-fallback (fal-ai/ace-step) — генерирует песню со словами.

        Используется когда minimax-music упал, а у нас есть lyrics
        (stable-audio инструментален и слова теряет).
        """
        ...

    async def submit_speech(
        self,
        *,
        text: str,
        voice: str | None,
        webhook_url: str | None,
        idempotency_key: str,
    ) -> FalSubmitResult: ...

    async def upload_to_storage(
        self,
        *,
        content_iter,
        filename: str,
        content_type: str,
    ) -> str: ...

    def parse_webhook_event(
        self, *, headers: Mapping[str, str], raw_body: bytes
    ) -> FalWebhookEvent: ...

    async def fetch_status(
        self, *, model: str, request_id: str
    ) -> FalStatusResult:
        """Опрос статуса задачи в fal queue API."""
        ...
