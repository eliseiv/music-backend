from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.music.models import TokenProduct
from app.music.repositories.products import TokenProductsRepository
from app.music.services.wallet_service import WalletBalance, WalletService


@dataclass
class TokenBalance:
    available: int
    reserved: int
    frozen: bool


class TokensService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        wallet_service: WalletService,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._wallet = wallet_service

    async def get_balance(self, user_id: UUID) -> TokenBalance:
        b: WalletBalance = await self._wallet.get_balance(user_id)
        return TokenBalance(
            available=b.available, reserved=b.reserved, frozen=b.frozen
        )

    async def list_active_products(self) -> list[TokenProduct]:
        async with self._sessionmaker() as session:
            repo = TokenProductsRepository(session)
            return await repo.list_active()
