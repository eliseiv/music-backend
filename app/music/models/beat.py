from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.music.enums import BeatGenre


class Beat(Base, TimestampMixin):
    __tablename__ = "beats"
    __table_args__ = (
        UniqueConstraint("audio_url", name="uq_beats_audio_url"),
        Index("ix_beats_genre_active_sort_order", "genre", "active", "sort_order"),
        Index("ix_beats_tags_gin", "tags", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    genre: Mapped[BeatGenre] = mapped_column(
        SAEnum(BeatGenre, name="beat_genre", native_enum=True), nullable=False
    )
    # Поджанры (house, edm, trap, lofi_hip_hop и т.п.) — для фильтрации в UI.
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    audio_url: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
