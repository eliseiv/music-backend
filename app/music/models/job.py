from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.music.enums import JobStage, JobStatus


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_user_id_created_at", "user_id", "created_at"),
        Index(
            "ix_jobs_active_status",
            "status",
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
        Index(
            "ix_jobs_provider_request_id",
            "provider_request_id",
            postgresql_where=text("provider_request_id IS NOT NULL"),
        ),
        Index(
            "uq_jobs_user_id_client_idempotency_key",
            "user_id",
            "client_idempotency_key",
            unique=True,
            postgresql_where=text("client_idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("music_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status", native_enum=True), nullable=False
    )
    stage: Mapped[JobStage | None] = mapped_column(
        SAEnum(JobStage, name="job_stage", native_enum=True), nullable=True
    )
    # current_stage — последний запущенный async stage. Используется в Pipeline,
    # чтобы webhook знал, какой stage он завершает.
    current_stage: Mapped[JobStage | None] = mapped_column(
        SAEnum(JobStage, name="job_stage", native_enum=True, create_type=False),
        nullable=True,
    )
    provider_model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    pricing_rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("pricing_rules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reserved_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    captured_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    store_stems: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # client_idempotency_key — значение из header'а Idempotency-Key.
    # Уникальный partial-индекс на (user_id, client_idempotency_key) при NOT NULL.
    client_idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
