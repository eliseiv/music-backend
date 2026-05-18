"""MusicUser — per-device identity for the iOS app.

This identity is intentionally **separate** from the synthetic `uuid5(API_KEY)`
that lives in `conversations.user_id`. The chat module owns one synthetic
"app-level" user; this table owns real per-device users keyed by the
`X-User-Id` header (Adapty profile id or equivalent).

No FK link is added between `conversations.user_id` and `music_users.id`.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class MusicUser(Base, TimestampMixin):
    __tablename__ = "music_users"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    external_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
