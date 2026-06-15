"""token_products.is_subscription: помечаем продукты-подписки

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-15

Подписочные продукты (vendor_product_id из Adapty, напр. week_6.99_nottrial)
дают токены за период, но НЕ должны показываться в каталоге токен-паков
/v1/tokens/products. Флаг отделяет их от разовых токен-паков.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "token_products",
        sa.Column(
            "is_subscription",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("token_products", "is_subscription")
