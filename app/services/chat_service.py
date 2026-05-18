from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.api.errors import (
    ConversationForbidden,
    ConversationNotFound,
    ValidationFailed,
)
from app.config import Settings
from app.db.enums import MessageRole
from app.providers.llm.base import LLMMessage, LLMProvider
from app.repositories.conversations import ConversationsRepository
from app.repositories.messages import MessagesRepository
from app.utils.text import estimate_tokens, normalize_message

logger = logging.getLogger(__name__)


@dataclass
class SendMessageResult:
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    assistant_text: str
    created_at: datetime


@dataclass
class CreateConversationResult:
    conversation_id: UUID
    created_at: datetime


class ChatService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm: LLMProvider,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._llm = llm
        self._settings = settings

    async def create_conversation(
        self, *, user_id: UUID, title: str | None
    ) -> CreateConversationResult:
        async with self._sessionmaker() as session:
            async with session.begin():
                repo = ConversationsRepository(session)
                conv = await repo.add(user_id=user_id, title=title)
            return CreateConversationResult(
                conversation_id=conv.id,
                created_at=conv.created_at,
            )

    async def send_message(
        self,
        *,
        user_id: UUID,
        message: str,
        conversation_id: UUID | None,
    ) -> SendMessageResult:
        message = normalize_message(message)
        if not message:
            raise ValidationFailed(
                "Message must not be empty",
                details={"field": "message"},
            )
        if len(message) > self._settings.MAX_MESSAGE_CHARS:
            raise ValidationFailed(
                "Message is too long",
                details={
                    "field": "message",
                    "max_chars": self._settings.MAX_MESSAGE_CHARS,
                },
            )

        async with self._sessionmaker() as session:
            async with session.begin():
                conversations = ConversationsRepository(session)
                messages = MessagesRepository(session)

                if conversation_id is not None:
                    conv = await conversations.get_by_id(conversation_id)
                    if conv is None:
                        raise ConversationNotFound()
                    if conv.user_id != user_id:
                        raise ConversationForbidden()
                else:
                    conv = await conversations.add(user_id=user_id, title=None)

                user_msg = await messages.add(
                    conversation_id=conv.id,
                    role=MessageRole.user,
                    text=message,
                )
                history = await messages.list_for_conversation(
                    conv.id, limit=self._settings.HISTORY_MAX_MESSAGES
                )
                user_msg_id = user_msg.id
                conv_id = conv.id

            llm_messages = self._build_llm_messages(history)
            assistant_text = await self._llm.chat(
                messages=llm_messages,
                model=self._settings.OPENAI_CHAT_MODEL,
                max_output_tokens=self._settings.LLM_MAX_OUTPUT_TOKENS,
                timeout=self._settings.LLM_CHAT_TIMEOUT_SECONDS,
            )
            assistant_text = (assistant_text or "").strip()
            if not assistant_text:
                assistant_text = ""

            async with session.begin():
                messages = MessagesRepository(session)
                assistant_msg = await messages.add(
                    conversation_id=conv_id,
                    role=MessageRole.assistant,
                    text=assistant_text,
                )

            return SendMessageResult(
                conversation_id=conv_id,
                user_message_id=user_msg_id,
                assistant_message_id=assistant_msg.id,
                assistant_text=assistant_text,
                created_at=assistant_msg.created_at,
            )

    def _build_llm_messages(self, history) -> list[LLMMessage]:  # type: ignore[no-untyped-def]
        budget = self._settings.LLM_MAX_INPUT_TOKENS
        system_prompt = self._settings.CHAT_SYSTEM_PROMPT
        budget -= estimate_tokens(system_prompt)

        kept: list[LLMMessage] = []
        for msg in reversed(history):
            cost = estimate_tokens(msg.text)
            if budget - cost < 0 and kept:
                break
            budget -= cost
            kept.append({"role": msg.role.value, "content": msg.text})  # type: ignore[typeddict-item]
        kept.reverse()

        if not kept:
            kept = [{"role": "user", "content": history[-1].text}]  # type: ignore[typeddict-item]

        return [{"role": "system", "content": system_prompt}, *kept]
