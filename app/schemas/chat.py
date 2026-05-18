from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.schemas.common import CamelModel
from app.utils.text import normalize_message


class CreateConversationRequest(CamelModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"title": "Идеи для новой песни"},
                {"title": "Mood board: летняя поп-баллада"},
                {},
            ]
        },
    )

    title: str | None = Field(
        default=None,
        max_length=200,
        description="Название conversation (опционально, до 200 символов).",
        examples=["Идеи для новой песни"],
    )

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped or None
        return v


class CreateConversationResponse(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "conversationId": "f2bf8c34-4125-4c98-a838-40c22fabb148",
                    "createdAt": "2026-05-08T14:39:08.169072Z",
                }
            ]
        },
    )

    conversation_id: UUID = Field(
        description="UUID созданного conversation.",
        examples=["f2bf8c34-4125-4c98-a838-40c22fabb148"],
    )
    created_at: datetime = Field(
        description="Момент создания (UTC, ISO 8601).",
        examples=["2026-05-08T14:39:08.169072Z"],
    )


class SendMessageRequest(CamelModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"message": "Привет! Подскажи рифмы к слову love."},
                {
                    "message": "And now suggest two more lines.",
                    "conversationId": "f2bf8c34-4125-4c98-a838-40c22fabb148",
                },
            ]
        },
    )

    message: str = Field(
        min_length=1,
        max_length=64_000,
        description=(
            "Текст сообщения пользователя. После нормализации (NFC, "
            "trim) длина должна быть не меньше 1 и не больше "
            "`MAX_MESSAGE_CHARS` (по умолчанию 8000)."
        ),
        examples=["Привет! Подскажи рифмы к слову love."],
    )
    conversation_id: UUID | None = Field(
        default=None,
        description=(
            "UUID существующего conversation. Если не передано, "
            "сервис создаст новый conversation автоматически."
        ),
        examples=["f2bf8c34-4125-4c98-a838-40c22fabb148"],
    )

    @field_validator("message", mode="before")
    @classmethod
    def _normalize(cls, v: object) -> object:
        if isinstance(v, str):
            normalized = normalize_message(v)
            return normalized
        return v


class SendMessageResponse(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "conversationId": "f2bf8c34-4125-4c98-a838-40c22fabb148",
                    "userMessageId": "7457f283-b166-4d29-830c-dae127dd799d",
                    "assistantMessageId": "2c0463b9-7692-4dd8-b9c1-a65611cb12b4",
                    "assistantText": "Sure! Try rhymes like dove, glove, above.",
                    "createdAt": "2026-05-08T14:41:33.667429Z",
                }
            ]
        },
    )

    conversation_id: UUID = Field(
        description="UUID conversation, в который добавлены оба сообщения.",
    )
    user_message_id: UUID = Field(
        description="UUID сохранённого сообщения пользователя.",
    )
    assistant_message_id: UUID = Field(
        description="UUID сохранённого ответа ассистента.",
    )
    assistant_text: str = Field(
        description="Текст ответа от LLM.",
        examples=["Sure! Try rhymes like dove, glove, above."],
    )
    created_at: datetime = Field(
        description="Момент создания ответа ассистента (UTC).",
    )
