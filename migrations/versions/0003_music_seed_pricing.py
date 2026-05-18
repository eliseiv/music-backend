"""music seed: pricing rules + token products

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-09

Inserts the canonical pricing rules and token products. Uses
ON CONFLICT DO NOTHING for idempotency — re-running the migration
on an already-seeded DB is safe.
"""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.music.seed import importers

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


_SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "music" / "seed" / "data"


def upgrade() -> None:
    bind = op.get_bind()

    pricing_rules = importers.parse_pricing_rules(_SEED_DIR / "pricing.json")
    for rule in pricing_rules:
        bind.execute(
            sa.text(
                """
                INSERT INTO pricing_rules (
                    provider_model, billing_mode, token_rate, rounding_mode,
                    precharge_default_units, active_from
                )
                VALUES (
                    :provider_model,
                    CAST(:billing_mode AS pricing_billing_mode),
                    :token_rate,
                    CAST(:rounding_mode AS rounding_mode),
                    :precharge_default_units,
                    :active_from
                )
                ON CONFLICT (provider_model, active_from) DO NOTHING
                """
            ),
            rule.to_row(),
        )

    token_products = importers.parse_token_products(
        _SEED_DIR / "token_products.json"
    )
    for product in token_products:
        bind.execute(
            sa.text(
                """
                INSERT INTO token_products (
                    code, platform, external_product_id, token_amount,
                    price_minor, currency, active
                )
                VALUES (
                    :code,
                    CAST(:platform AS billing_platform),
                    :external_product_id,
                    :token_amount,
                    :price_minor,
                    :currency,
                    :active
                )
                ON CONFLICT (code) DO NOTHING
                """
            ),
            product.to_row(),
        )


def downgrade() -> None:
    bind = op.get_bind()
    pricing_rules = importers.parse_pricing_rules(_SEED_DIR / "pricing.json")
    for rule in pricing_rules:
        bind.execute(
            sa.text(
                "DELETE FROM pricing_rules "
                "WHERE provider_model = :provider_model "
                "AND active_from = :active_from"
            ),
            {"provider_model": rule.provider_model, "active_from": rule.active_from},
        )
    token_products = importers.parse_token_products(
        _SEED_DIR / "token_products.json"
    )
    for product in token_products:
        bind.execute(
            sa.text("DELETE FROM token_products WHERE code = :code"),
            {"code": product.code},
        )
