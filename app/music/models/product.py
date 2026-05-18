from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum as SAEnum,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.music.enums import BillingPlatform


class TokenProduct(Base, TimestampMixin):
    __tablename__ = "token_products"
    __table_args__ = (
        UniqueConstraint("code", name="uq_token_products_code"),
        UniqueConstraint(
            "platform",
            "external_product_id",
            name="uq_token_products_platform_external",
        ),
        CheckConstraint("token_amount > 0", name="ck_token_products_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[BillingPlatform] = mapped_column(
        SAEnum(BillingPlatform, name="billing_platform", native_enum=True),
        nullable=False,
    )
    external_product_id: Mapped[str] = mapped_column(String(160), nullable=False)
    token_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
