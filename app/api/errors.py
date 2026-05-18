from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_config import request_id_var
from app.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)


class APIError(Exception):
    code: str = "error"
    http_status: int = 500
    message: str = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.details = details
        super().__init__(self.message)


class ValidationFailed(APIError):
    code = "validation_error"
    http_status = 400
    message = "Validation failed"


class InvalidQueryForCriterion(APIError):
    code = "invalid_query_for_criterion"
    http_status = 422
    message = "Query is not suitable for the selected criterion"


class ConversationNotFound(APIError):
    code = "conversation_not_found"
    http_status = 404
    message = "Conversation not found"


class ConversationForbidden(APIError):
    code = "conversation_forbidden"
    http_status = 403
    message = "Conversation does not belong to the current user"


class RateLimited(APIError):
    code = "rate_limited"
    http_status = 429
    message = "Rate limit exceeded"


class LLMTimeout(APIError):
    code = "llm_timeout"
    http_status = 504
    message = "LLM provider timed out"


class LLMProviderError(APIError):
    code = "llm_provider_error"
    http_status = 502
    message = "LLM provider failed"


class AuthError(APIError):
    code = "auth_error"
    http_status = 401
    message = "Invalid or missing API key"


# --- Music module errors ---


class MissingXUserId(APIError):
    code = "missing_x_user_id"
    http_status = 400
    message = "Missing or invalid X-User-Id header"


class SubscriptionInactive(APIError):
    code = "subscription_inactive"
    http_status = 402
    message = "Active subscription required"


class InsufficientTokens(APIError):
    code = "insufficient_tokens"
    http_status = 402
    message = "Not enough tokens to perform the operation"


class JobNotFound(APIError):
    code = "job_not_found"
    http_status = 404
    message = "Generation job not found"


class JobForbidden(APIError):
    code = "job_forbidden"
    http_status = 403
    message = "Generation job belongs to another user"


class TrackNotFound(APIError):
    code = "track_not_found"
    http_status = 404
    message = "Track not found"


class BeatNotFound(APIError):
    code = "beat_not_found"
    http_status = 404
    message = "Beat not found"


class WebhookSignatureInvalid(APIError):
    code = "webhook_signature_invalid"
    http_status = 401
    message = "Webhook signature verification failed"


class WebhookPayloadInvalid(APIError):
    code = "webhook_payload_invalid"
    http_status = 400
    message = "Webhook payload is malformed"


class PricingRuleMissing(APIError):
    code = "pricing_rule_missing"
    http_status = 500
    message = "No active pricing rule configured for the provider model"


class FalProviderError(APIError):
    code = "fal_provider_error"
    http_status = 502
    message = "fal.ai provider returned an error"


class FalTimeout(APIError):
    code = "fal_timeout"
    http_status = 504
    message = "fal.ai provider timed out"


def _envelope(
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None,
    request_id: str | None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        code=code,
        message=message,
        details=details,
        requestId=request_id,
    ).model_dump(by_alias=True, exclude_none=True)
    return JSONResponse(body, status_code=status_code, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        if exc.http_status >= 500:
            logger.exception("API error: %s", exc.message)
        else:
            logger.info("API error: %s (%s)", exc.code, exc.message)
        headers: dict[str, str] | None = None
        if isinstance(exc, RateLimited) and exc.details:
            retry_after = exc.details.get("retry_after_seconds")
            if retry_after is not None:
                headers = {"Retry-After": str(int(retry_after))}
        return _envelope(
            code=exc.code,
            message=exc.message,
            status_code=exc.http_status,
            details=exc.details,
            request_id=request_id_var.get(),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _envelope(
            code="validation_error",
            message="Request validation failed",
            status_code=400,
            details={"errors": exc.errors()},
            request_id=request_id_var.get(),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        return _envelope(
            code="internal_error",
            message="Internal server error",
            status_code=500,
            details=None,
            request_id=request_id_var.get(),
        )
