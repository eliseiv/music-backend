from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": {
                        "errors": [
                            {
                                "type": "string_too_short",
                                "loc": ["body", "message"],
                                "msg": "String should have at least 1 character",
                            }
                        ]
                    },
                    "requestId": "b5830b11dc4747d4b6b85217eff10177",
                },
                {
                    "code": "auth_error",
                    "message": "Invalid or missing API key",
                    "requestId": "ecb265cdcce14a889ebcf1c5c8665068",
                },
                {
                    "code": "conversation_not_found",
                    "message": "Conversation not found",
                    "requestId": "71244931bce24d31b0914c3933838cdf",
                },
            ]
        },
    )

    code: str = Field(
        description=(
            "Машинно-читаемый код ошибки "
            "(validation_error, auth_error, conversation_not_found, ...)."
        ),
        examples=["validation_error"],
    )
    message: str = Field(
        description="Человеко-читаемое описание ошибки.",
        examples=["Request validation failed"],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Дополнительные детали (опционально).",
    )
    request_id: str | None = Field(
        default=None,
        alias="requestId",
        description="ID запроса (тот же, что в заголовке `X-Request-Id`).",
        examples=["b5830b11dc4747d4b6b85217eff10177"],
    )
