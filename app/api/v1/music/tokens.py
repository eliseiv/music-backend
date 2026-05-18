from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.music._common import MUSIC_ERROR_RESPONSES
from app.deps import (
    get_music_user,
    get_sessionmaker,
    get_wallet_service,
)
from app.music.models import MusicUser
from app.music.schemas.tokens import (
    TokenBalanceResponse,
    TokenProductItem,
    TokenProductsResponse,
)
from app.music.services.tokens_service import TokensService
from app.music.services.wallet_service import WalletService

router = APIRouter(tags=["music-tokens"])


def _get_tokens_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
    wallet: Annotated[WalletService, Depends(get_wallet_service)],
) -> TokensService:
    return TokensService(sessionmaker, wallet)


@router.get(
    "/tokens/balance",
    response_model=TokenBalanceResponse,
    response_model_by_alias=True,
    summary="Баланс токенов текущего пользователя",
    description="Возвращает `available`, `reserved` и флаг `frozen` (true, если подписка истекла).",
    responses={
        k: v for k, v in MUSIC_ERROR_RESPONSES.items() if k in {400, 401}
    },
)
async def token_balance(
    user: Annotated[MusicUser, Depends(get_music_user)],
    tokens: Annotated[TokensService, Depends(_get_tokens_service)],
) -> TokenBalanceResponse:
    balance = await tokens.get_balance(user.id)
    return TokenBalanceResponse(
        available=balance.available,
        reserved=balance.reserved,
        frozen=balance.frozen,
    )


@router.get(
    "/tokens/products",
    response_model=TokenProductsResponse,
    response_model_by_alias=True,
    summary="Каталог токен-паков",
    description=(
        "Список активных продуктов для покупки токенов через Adapty/RuStore. "
        "iOS использует `external_product_id` при покупке."
    ),
    responses={
        k: v for k, v in MUSIC_ERROR_RESPONSES.items() if k in {400, 401}
    },
)
async def token_products(
    user: Annotated[MusicUser, Depends(get_music_user)],
    tokens: Annotated[TokensService, Depends(_get_tokens_service)],
) -> TokenProductsResponse:
    products = await tokens.list_active_products()
    return TokenProductsResponse(
        products=[TokenProductItem.model_validate(p) for p in products]
    )
