from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Index,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.music.enums import WebhookProvider


class ProcessedWebhook(Base):
    __tablename__ = "processed_webhooks"
    __table_args__ = (
        PrimaryKeyConstraint("provider", "event_id", name="pk_processed_webhooks"),
        Index("ix_processed_webhooks_received_at", "received_at"),
    )

    provider: Mapped[WebhookProvider] = mapped_column(
        SAEnum(WebhookProvider, name="webhook_provider", native_enum=True),
        nullable=False,
    )
    event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
