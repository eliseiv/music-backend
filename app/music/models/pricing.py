from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.music.enums import BillingMode, RoundingMode


class PricingRule(Base):
    __tablename__ = "pricing_rules"
    __table_args__ = (
        UniqueConstraint(
            "provider_model",
            "active_from",
            name="uq_pricing_rules_model_active_from",
        ),
        Index(
            "ix_pricing_rules_model_active_from",
            "provider_model",
            "active_from",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    provider_model: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_mode: Mapped[BillingMode] = mapped_column(
        SAEnum(BillingMode, name="pricing_billing_mode", native_enum=True),
        nullable=False,
    )
    token_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    rounding_mode: Mapped[RoundingMode] = mapped_column(
        SAEnum(RoundingMode, name="rounding_mode", native_enum=True),
        nullable=False,
        default=RoundingMode.ceil,
        server_default=RoundingMode.ceil.value,
    )
    precharge_default_units: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    active_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
