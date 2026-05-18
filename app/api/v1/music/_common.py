"""Общие responses-словари для всех music-эндпоинтов."""
from __future__ import annotations

from app.schemas.common import ErrorResponse

MUSIC_ERROR_RESPONSES = {
    400: {
        "model": ErrorResponse,
        "description": "Ошибка валидации входных данных или отсутствует X-User-Id",
    },
    401: {"model": ErrorResponse, "description": "Нет/неверный API_KEY"},
    402: {
        "model": ErrorResponse,
        "description": "Подписка неактивна или недостаточно токенов",
    },
    403: {
        "model": ErrorResponse,
        "description": "Запрашиваемый ресурс принадлежит другому пользователю",
    },
    404: {"model": ErrorResponse, "description": "Ресурс не найден"},
    422: {
        "model": ErrorResponse,
        "description": "Параметры запроса не подходят под выбранные условия",
    },
    429: {"model": ErrorResponse, "description": "Превышен лимит запросов"},
    502: {"model": ErrorResponse, "description": "Ошибка внешнего провайдера"},
    504: {"model": ErrorResponse, "description": "Таймаут внешнего провайдера"},
}
