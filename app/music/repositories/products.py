from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.models import TokenProduct


class TokenProductsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[TokenProduct]:
        """Все активные продукты (паки + подписки) — для резолва токенов."""
        stmt = (
            select(TokenProduct)
            .where(TokenProduct.active.is_(True))
            .order_by(TokenProduct.token_amount)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_purchasable(self) -> list[TokenProduct]:
        """Только токен-паки для каталога /v1/tokens/products
        (подписки исключены — они покупаются как подписки, не как паки)."""
        stmt = (
            select(TokenProduct)
            .where(
                TokenProduct.active.is_(True),
                TokenProduct.is_subscription.is_(False),
            )
            .order_by(TokenProduct.token_amount)
        )
        return list((await self._session.execute(stmt)).scalars().all())
