from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation


class ConversationsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, *, user_id: UUID, title: str | None) -> Conversation:
        conv = Conversation(user_id=user_id, title=title)
        self._session.add(conv)
        await self._session.flush()
        await self._session.refresh(conv)
        return conv

    async def get_by_id(self, conv_id: UUID) -> Conversation | None:
        return await self._session.get(Conversation, conv_id)

    async def get_for_user(
        self, conv_id: UUID, user_id: UUID
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conv_id, Conversation.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
