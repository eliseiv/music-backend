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
from app.music.enums import SampleCategory


class Sample(Base, TimestampMixin):
    __tablename__ = "samples"
    __table_args__ = (
        UniqueConstraint("audio_url", name="uq_samples_audio_url"),
        Index("ix_samples_category_active", "category", "active"),
        Index(
            "ix_samples_tags_gin",
            "tags",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    category: Mapped[SampleCategory] = mapped_column(
        SAEnum(SampleCategory, name="sample_category", native_enum=True),
        nullable=False,
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    audio_url: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
