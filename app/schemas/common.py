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


class ErrorDetail(BaseModel):
    """Тело ошибки внутри обёртки `error`."""

    model_config = ConfigDict(populate_by_name=True)

    code: str = Field(
        description=(
            "Машинно-читаемый код ошибки в UPPER_SNAKE_CASE "
            "(INVALID_INPUT, SUBSCRIPTION_REQUIRED, INSUFFICIENT_TOKENS, ...)."
        ),
        examples=["INVALID_INPUT"],
    )
    message: str = Field(
        description="Человеко-читаемое описание ошибки.",
        examples=["Request validation failed"],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Дополнительные детали (опционально).",
    )


class ErrorResponse(BaseModel):
    """Формат ошибок `{"error": {...}, "requestId": "..."}`."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "INVALID_INPUT",
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
                    },
                    "requestId": "b5830b11dc4747d4b6b85217eff10177",
                },
                {
                    "error": {
                        "code": "INSUFFICIENT_TOKENS",
                        "message": "Not enough tokens to generate track",
                        "details": {"required": 2, "available": 0},
                    },
                    "requestId": "ecb265cdcce14a889ebcf1c5c8665068",
                },
            ]
        },
    )

    error: ErrorDetail
    request_id: str | None = Field(
        default=None,
        alias="requestId",
        description="ID запроса (тот же, что в заголовке `X-Request-Id`).",
        examples=["b5830b11dc4747d4b6b85217eff10177"],
    )
