from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import MessageRole
from app.models.message import Message


class MessagesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, conversation_id: UUID, role: MessageRole, text: str
    ) -> Message:
        msg = Message(conversation_id=conversation_id, role=role, text=text)
        self._session.add(msg)
        await self._session.flush()
        await self._session.refresh(msg)
        return msg

    async def list_for_conversation(
        self, conversation_id: UUID, *, limit: int
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(reversed(rows))
