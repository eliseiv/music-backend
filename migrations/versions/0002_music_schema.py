"""music schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-09

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


# Enum definitions — created with create_type=True only inside CREATE TABLE,
# so we declare them with create_type=False elsewhere when reused.
def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    music_users = op.create_table(
        "music_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("external_id", name="uq_music_users_external_id"),
    )
    op.create_index(
        op.f("ix_music_users_external_id"), "music_users", ["external_id"]
    )

    # --- enums ---
    subscription_status = _enum(
        "subscription_status", "none", "active", "canceled", "expired"
    )
    subscription_status.create(op.get_bind(), checkfirst=True)

    billing_provider = _enum("billing_provider", "adapty", "rustore")
    billing_provider.create(op.get_bind(), checkfirst=True)

    billing_platform = _enum("billing_platform", "adapty", "rustore")
    billing_platform.create(op.get_bind(), checkfirst=True)

    pricing_billing_mode = _enum("pricing_billing_mode", "per_track", "per_minute")
    pricing_billing_mode.create(op.get_bind(), checkfirst=True)

    rounding_mode = _enum("rounding_mode", "ceil", "floor", "nearest")
    rounding_mode.create(op.get_bind(), checkfirst=True)

    token_ledger_kind = _enum(
        "token_ledger_kind",
        "credit_purchase",
        "credit_subscription_grant",
        "debit_reserve",
        "debit_capture",
        "credit_release",
        "credit_refund",
        "debit_adjustment",
        "credit_adjustment",
    )
    token_ledger_kind.create(op.get_bind(), checkfirst=True)

    beat_genre = _enum(
        "beat_genre",
        "electronic_dance",
        "rap",
        "lofi",
        "global_groove",
        "relaxing_meditation",
    )
    beat_genre.create(op.get_bind(), checkfirst=True)

    sample_category = _enum(
        "sample_category",
        "harmonic_bass",
        "harmonic_lead",
        "harmonic_chord",
        "drums_kick",
        "drums_snare",
        "drums_closed_hihat",
        "drums_open_hihat",
        "drums_auxiliary",
        "mixing",
        "sound_effects",
    )
    sample_category.create(op.get_bind(), checkfirst=True)

    job_status = _enum(
        "job_status", "queued", "processing", "succeeded", "failed", "canceled"
    )
    job_status.create(op.get_bind(), checkfirst=True)

    job_stage = _enum(
        "job_stage",
        "prepare_prompt",
        "lyrics",
        "music_generation",
        "audio_to_audio_refine",
        "vocal_tts",
        "mix_master",
        "upload_cdn",
        "finalize",
    )
    job_stage.create(op.get_bind(), checkfirst=True)

    webhook_provider = _enum("webhook_provider", "fal", "adapty", "rustore")
    webhook_provider.create(op.get_bind(), checkfirst=True)

    # --- token_wallets ---
    op.create_table(
        "token_wallets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "available_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reserved_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "frozen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "available_tokens >= 0", name="ck_token_wallets_available_nonneg"
        ),
        sa.CheckConstraint(
            "reserved_tokens >= 0", name="ck_token_wallets_reserved_nonneg"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["music_users.id"],
            name="fk_token_wallets_user_id_music_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", name="uq_token_wallets_user_id"),
    )
    op.create_index(
        op.f("ix_token_wallets_user_id"), "token_wallets", ["user_id"]
    )

    # --- subscription_state ---
    op.create_table(
        "subscription_state",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="subscription_status", create_type=False),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "provider",
            postgresql.ENUM(name="billing_provider", create_type=False),
            nullable=True,
        ),
        sa.Column("product_external_id", sa.String(length=160), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_id", sa.String(length=160), nullable=True),
        sa.Column(
            "last_event_occurred_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["music_users.id"],
            name="fk_subscription_state_user_id_music_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_subscription_state"),
    )
    op.create_index(
        "ix_subscription_state_status_expires_at",
        "subscription_state",
        ["status", "expires_at"],
    )

    # --- pricing_rules ---
    op.create_table(
        "pricing_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider_model", sa.String(length=128), nullable=False),
        sa.Column(
            "billing_mode",
            postgresql.ENUM(name="pricing_billing_mode", create_type=False),
            nullable=False,
        ),
        sa.Column("token_rate", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "rounding_mode",
            postgresql.ENUM(name="rounding_mode", create_type=False),
            nullable=False,
            server_default="ceil",
        ),
        sa.Column("precharge_default_units", sa.Numeric(8, 2), nullable=True),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider_model",
            "active_from",
            name="uq_pricing_rules_model_active_from",
        ),
    )
    op.create_index(
        "ix_pricing_rules_model_active_from",
        "pricing_rules",
        ["provider_model", "active_from"],
    )

    # --- token_products ---
    op.create_table(
        "token_products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(name="billing_platform", create_type=False),
            nullable=False,
        ),
        sa.Column("external_product_id", sa.String(length=160), nullable=False),
        sa.Column("token_amount", sa.BigInteger(), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "token_amount > 0", name="ck_token_products_amount_positive"
        ),
        sa.UniqueConstraint("code", name="uq_token_products_code"),
        sa.UniqueConstraint(
            "platform",
            "external_product_id",
            name="uq_token_products_platform_external",
        ),
    )

    # --- beats ---
    op.create_table(
        "beats",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "genre",
            postgresql.ENUM(name="beat_genre", create_type=False),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("audio_url", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("bpm", sa.Integer(), nullable=True),
        sa.Column("key", sa.String(length=16), nullable=True),
        sa.Column("preview_url", sa.Text(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("audio_url", name="uq_beats_audio_url"),
    )
    op.create_index(
        "ix_beats_genre_active_sort_order",
        "beats",
        ["genre", "active", "sort_order"],
    )

    # --- samples ---
    op.create_table(
        "samples",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "category",
            postgresql.ENUM(name="sample_category", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("audio_url", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("audio_url", name="uq_samples_audio_url"),
    )
    op.create_index(
        "ix_samples_category_active",
        "samples",
        ["category", "active"],
    )
    op.create_index(
        "ix_samples_tags_gin",
        "samples",
        ["tags"],
        postgresql_using="gin",
    )

    # --- jobs ---
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="job_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "stage",
            postgresql.ENUM(name="job_stage", create_type=False),
            nullable=True,
        ),
        sa.Column("provider_model", sa.String(length=128), nullable=False),
        sa.Column("provider_request_id", sa.String(length=160), nullable=True),
        sa.Column(
            "pricing_rule_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "reserved_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "captured_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("input_payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "store_stems",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["music_users.id"],
            name="fk_jobs_user_id_music_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pricing_rule_id"],
            ["pricing_rules.id"],
            name="fk_jobs_pricing_rule_id_pricing_rules",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_jobs_user_id_created_at", "jobs", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_jobs_active_status",
        "jobs",
        ["status"],
        postgresql_where=sa.text("status IN ('queued', 'processing')"),
    )
    op.create_index(
        "ix_jobs_provider_request_id",
        "jobs",
        ["provider_request_id"],
        postgresql_where=sa.text("provider_request_id IS NOT NULL"),
    )

    # --- tracks ---
    op.create_table(
        "tracks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audio_url", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(8, 2), nullable=False),
        sa.Column("stems", postgresql.JSONB, nullable=True),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_tracks_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["music_users.id"],
            name="fk_tracks_user_id_music_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("job_id", name="uq_tracks_job_id"),
    )
    op.create_index(
        "ix_tracks_user_id_created_at", "tracks", ["user_id", "created_at"]
    )

    # --- token_ledger ---
    op.create_table(
        "token_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(name="token_ledger_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_available", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_reserved", sa.BigInteger(), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=192), nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["music_users.id"],
            name="fk_token_ledger_user_id_music_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["token_wallets.id"],
            name="fk_token_ledger_wallet_id_token_wallets",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_token_ledger_idempotency_key"
        ),
    )
    op.create_index(
        "ix_token_ledger_user_id_created_at",
        "token_ledger",
        ["user_id", "created_at"],
    )
    op.create_index(
        op.f("ix_token_ledger_user_id"), "token_ledger", ["user_id"]
    )

    # --- processed_webhooks ---
    op.create_table(
        "processed_webhooks",
        sa.Column(
            "provider",
            postgresql.ENUM(name="webhook_provider", create_type=False),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=True),
        sa.PrimaryKeyConstraint(
            "provider", "event_id", name="pk_processed_webhooks"
        ),
    )
    op.create_index(
        "ix_processed_webhooks_received_at",
        "processed_webhooks",
        ["received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processed_webhooks_received_at", table_name="processed_webhooks"
    )
    op.drop_table("processed_webhooks")

    op.drop_index(op.f("ix_token_ledger_user_id"), table_name="token_ledger")
    op.drop_index(
        "ix_token_ledger_user_id_created_at", table_name="token_ledger"
    )
    op.drop_table("token_ledger")

    op.drop_index("ix_tracks_user_id_created_at", table_name="tracks")
    op.drop_table("tracks")

    op.drop_index("ix_jobs_provider_request_id", table_name="jobs")
    op.drop_index("ix_jobs_active_status", table_name="jobs")
    op.drop_index("ix_jobs_user_id_created_at", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_samples_tags_gin", table_name="samples")
    op.drop_index("ix_samples_category_active", table_name="samples")
    op.drop_table("samples")

    op.drop_index("ix_beats_genre_active_sort_order", table_name="beats")
    op.drop_table("beats")

    op.drop_table("token_products")

    op.drop_index(
        "ix_pricing_rules_model_active_from", table_name="pricing_rules"
    )
    op.drop_table("pricing_rules")

    op.drop_index(
        "ix_subscription_state_status_expires_at",
        table_name="subscription_state",
    )
    op.drop_table("subscription_state")

    op.drop_index(op.f("ix_token_wallets_user_id"), table_name="token_wallets")
    op.drop_table("token_wallets")

    op.drop_index(op.f("ix_music_users_external_id"), table_name="music_users")
    op.drop_table("music_users")

    bind = op.get_bind()
    for enum_name in (
        "webhook_provider",
        "job_stage",
        "job_status",
        "sample_category",
        "beat_genre",
        "token_ledger_kind",
        "rounding_mode",
        "pricing_billing_mode",
        "billing_platform",
        "billing_provider",
        "subscription_status",
    ):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
