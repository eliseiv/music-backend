from __future__ import annotations

from uuid import UUID

from pydantic import ConfigDict, Field

from app.music.enums import BillingPlatform
from app.schemas.common import CamelModel


class TokenBalanceResponse(CamelModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"available": 42, "reserved": 1, "frozen": False}]
        }
    )

    available: int = Field(description="Доступно токенов.")
    reserved: int = Field(description="Зарезервировано (под текущие задания).")
    frozen: bool = Field(description="Заморожен ли кошелёк (подписка истекла).")


class TokenProductItem(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "uuid",
                    "code": "tokens_10",
                    "platform": "adapty",
                    "externalProductId": "com.appstorepro.tokens_10",
                    "tokenAmount": 10,
                    "priceMinor": 99,
                    "currency": "USD",
                }
            ]
        },
    )

    id: UUID
    code: str
    platform: BillingPlatform
    external_product_id: str
    token_amount: int
    price_minor: int | None = None
    currency: str | None = None


class TokenProductsResponse(CamelModel):
    products: list[TokenProductItem]
