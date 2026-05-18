"""jobs pipeline extensions: current_stage, client_idempotency_key, job_stage_log

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-18

- ALTER TABLE jobs: добавить current_stage (job_stage NULL) и
  client_idempotency_key (VARCHAR(128) NULL).
- Уникальный partial-индекс на (user_id, client_idempotency_key) при NOT NULL.
- CREATE TABLE job_stage_log: append-only журнал стадий пайплайна.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. jobs.current_stage (повторно используем существующий enum job_stage)
    op.add_column(
        "jobs",
        sa.Column(
            "current_stage",
            postgresql.ENUM(name="job_stage", create_type=False),
            nullable=True,
        ),
    )

    # 2. jobs.client_idempotency_key + partial unique index
    op.add_column(
        "jobs",
        sa.Column("client_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_jobs_user_id_client_idempotency_key",
        "jobs",
        ["user_id", "client_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("client_idempotency_key IS NOT NULL"),
    )

    # 3. job_stage_log
    op.create_table(
        "job_stage_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "stage",
            postgresql.ENUM(name="job_stage", create_type=False),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_stage_log_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", "stage", name="uq_job_stage_log_job_stage"),
    )
    op.create_index("ix_job_stage_log_job_id", "job_stage_log", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_stage_log_job_id", table_name="job_stage_log")
    op.drop_table("job_stage_log")
    op.drop_index(
        "uq_jobs_user_id_client_idempotency_key", table_name="jobs"
    )
    op.drop_column("jobs", "client_idempotency_key")
    op.drop_column("jobs", "current_stage")
