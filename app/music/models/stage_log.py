from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.music.enums import JobStage


class JobStageLog(Base):
    """Журнал стадий пайплайна для job — append-only.

    Для каждой стадии хранится один record (UNIQUE(job_id, stage)). Статус
    обновляется in-place при переходах pending → running → succeeded/failed/skipped.
    """

    __tablename__ = "job_stage_log"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", name="uq_job_stage_log_job_stage"),
        Index("ix_job_stage_log_job_id", "job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[JobStage] = mapped_column(
        SAEnum(JobStage, name="job_stage", native_enum=True, create_type=False),
        nullable=False,
    )
    # "pending" | "running" | "succeeded" | "failed" | "skipped"
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
