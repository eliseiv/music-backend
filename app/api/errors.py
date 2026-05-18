from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_config import request_id_var
from app.schemas.common import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class APIError(Exception):
    code: str = "ERROR"
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


# --- Generic ---


class ValidationFailed(APIError):
    code = "INVALID_INPUT"
    http_status = 400
    message = "Validation failed"


class RateLimited(APIError):
    code = "RATE_LIMITED"
    http_status = 429
    message = "Rate limit exceeded"


class AuthError(APIError):
    code = "UNAUTHORIZED"
    http_status = 401
    message = "Invalid or missing API key"


# --- Music: header / access ---


class MissingXUserId(APIError):
    code = "MISSING_X_USER_ID"
    http_status = 400
    message = "Missing or invalid X-User-Id header"


class Forbidden(APIError):
    code = "FORBIDDEN"
    http_status = 403
    message = "Resource belongs to another user"


# --- Music: subscription / tokens ---


class SubscriptionRequired(APIError):
    code = "SUBSCRIPTION_REQUIRED"
    http_status = 402
    message = "Active subscription required"


class SubscriptionExpired(SubscriptionRequired):
    """Частный случай: подписка была, но истекла.

    Наследуется от SubscriptionRequired — `except SubscriptionRequired`
    отлавливает оба класса.
    """

    code = "SUBSCRIPTION_EXPIRED"
    http_status = 402
    message = "Subscription has expired"


# Backward-compat alias: некоторые внутренние участки кода до рефакторинга
# поднимали SubscriptionInactive. Оставляем как алиас на SubscriptionRequired,
# чтобы старый код продолжил работать; при необходимости явно используйте
# SubscriptionExpired для истёкших подписок.
SubscriptionInactive = SubscriptionRequired


class InsufficientTokens(APIError):
    code = "INSUFFICIENT_TOKENS"
    http_status = 402
    message = "Not enough tokens to perform the operation"


# --- Music: resources ---


class JobNotFound(APIError):
    code = "JOB_NOT_FOUND"
    http_status = 404
    message = "Generation job not found"


class JobForbidden(APIError):
    code = "FORBIDDEN"
    http_status = 403
    message = "Generation job belongs to another user"


class TrackNotFound(APIError):
    code = "TRACK_NOT_FOUND"
    http_status = 404
    message = "Track not found"


class BeatNotFound(APIError):
    code = "BEAT_NOT_FOUND"
    http_status = 404
    message = "Beat not found"


# --- Music: validation ---


class InvalidSampleUrl(APIError):
    code = "INVALID_SAMPLE_URL"
    http_status = 400
    message = "Sample URL is not reachable or not allowed"


# --- Music: webhooks ---


class WebhookSignatureInvalid(APIError):
    code = "WEBHOOK_SIGNATURE_INVALID"
    http_status = 401
    message = "Webhook signature verification failed"


class WebhookPayloadInvalid(APIError):
    code = "WEBHOOK_PAYLOAD_INVALID"
    http_status = 400
    message = "Webhook payload is malformed"


# --- Music: internal ---


class PricingRuleMissing(APIError):
    code = "PRICING_RULE_MISSING"
    http_status = 500
    message = "No active pricing rule configured for the provider model"


class FalProviderError(APIError):
    code = "PROVIDER_FAILED"
    http_status = 502
    message = "fal.ai provider returned an error"


class FalTimeout(APIError):
    code = "PROVIDER_TIMEOUT"
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
        error=ErrorDetail(code=code, message=message, details=details),
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
            code="INVALID_INPUT",
            message="Request validation failed",
            status_code=400,
            details={"errors": exc.errors()},
            request_id=request_id_var.get(),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        return _envelope(
            code="INTERNAL_ERROR",
            message="Internal server error",
            status_code=500,
            details=None,
            request_id=request_id_var.get(),
        )
