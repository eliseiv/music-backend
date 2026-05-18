from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.music.models import TokenProduct


class TokenProductsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[TokenProduct]:
        stmt = (
            select(TokenProduct)
            .where(TokenProduct.active.is_(True))
            .order_by(TokenProduct.token_amount)
        )
        return list((await self._session.execute(stmt)).scalars().all())
