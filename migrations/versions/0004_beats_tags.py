"""beats.tags: добавляем массив тегов (поджанров) для битов

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-19

iOS-интегратор просил поджанровые теги: house, edm, trap и т.п.
Структура аналогична samples.tags — TEXT[] с GIN-индексом.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "beats",
        sa.Column(
            "tags",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_index(
        "ix_beats_tags_gin",
        "beats",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_beats_tags_gin", table_name="beats")
    op.drop_column("beats", "tags")
