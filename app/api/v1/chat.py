from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.deps import get_chat_service, get_current_user
from app.schemas.chat import (
    CreateConversationRequest,
    CreateConversationResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.schemas.common import ErrorResponse
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])

_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Ошибка валидации входных данных"},
    401: {"model": ErrorResponse, "description": "Нет или неверный API-ключ"},
    403: {
        "model": ErrorResponse,
        "description": "Conversation принадлежит другому пользователю",
    },
    404: {"model": ErrorResponse, "description": "Conversation не найден"},
    429: {"model": ErrorResponse, "description": "Превышен лимит запросов"},
    502: {"model": ErrorResponse, "description": "Ошибка LLM-провайдера"},
    504: {"model": ErrorResponse, "description": "Таймаут LLM-провайдера"},
}


@router.post(
    "/chat/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateConversationResponse,
    response_model_by_alias=True,
    responses={
        401: _ERROR_RESPONSES[401],
        429: _ERROR_RESPONSES[429],
    },
    summary="Создать новый conversation",
    description=(
        "Создаёт пустой conversation, привязанный к текущему пользователю "
        "(определяется по `Authorization: Bearer <API_KEY>`).\n\n"
        "Поле `title` опционально — его можно оставить пустым или "
        "не передавать вовсе. Возвращает UUID и время создания, "
        "которые потом используются в `POST /chat/messages`."
    ),
)
async def create_conversation(
    body: CreateConversationRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> CreateConversationResponse:
    result = await service.create_conversation(user_id=user_id, title=body.title)
    return CreateConversationResponse(
        conversation_id=result.conversation_id,
        created_at=result.created_at,
    )


@router.post(
    "/chat/messages",
    status_code=status.HTTP_200_OK,
    response_model=SendMessageResponse,
    response_model_by_alias=True,
    responses=_ERROR_RESPONSES,
    summary="Отправить сообщение и получить ответ AI",
    description=(
        "Отправляет сообщение пользователя в LLM и возвращает ответ "
        "ассистента. Логика работы с conversation:\n\n"
        "- Если `conversationId` **передан**, сообщение добавляется "
        "в существующий conversation. Conversation должен принадлежать "
        "текущему пользователю, иначе вернётся `403`. "
        "Если такого conversation нет — `404`.\n"
        "- Если `conversationId` **не передан**, сервис автоматически "
        "создаёт новый conversation и возвращает его UUID в ответе.\n\n"
        "В LLM передаётся история до `HISTORY_MAX_MESSAGES` последних "
        "сообщений (по умолчанию 30) — ассистент учитывает контекст. "
        "При таймауте LLM возвращается `504`, при иной ошибке "
        "провайдера — `502`."
    ),
)
async def send_message(
    body: SendMessageRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> SendMessageResponse:
    result = await service.send_message(
        user_id=user_id,
        message=body.message,
        conversation_id=body.conversation_id,
    )
    return SendMessageResponse(
        conversation_id=result.conversation_id,
        user_message_id=result.user_message_id,
        assistant_message_id=result.assistant_message_id,
        assistant_text=result.assistant_text,
        created_at=result.created_at,
    )
