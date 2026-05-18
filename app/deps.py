from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.api.errors import AuthError, MissingXUserId
from app.auth.api_keys import ApiKeyResolver
from app.config import Settings, get_settings
from app.providers.llm.base import LLMProvider
from app.providers.word_tools.base import WordToolsProvider
from app.services.chat_service import ChatService
from app.services.word_tools_service import WordToolsService
from app.music.models import MusicUser
from app.music.repositories.users import MusicUsersRepository
from app.music.services.pricing_service import PricingService
from app.music.services.subscription_gate import SubscriptionGate
from app.music.services.wallet_service import WalletService

bearer_scheme = HTTPBearer(auto_error=False, description="API key from .env")


def get_settings_dep() -> Settings:
    return get_settings()


def get_resolver(request: Request) -> ApiKeyResolver:
    resolver = getattr(request.app.state, "api_key_resolver", None)
    if not isinstance(resolver, ApiKeyResolver):
        raise RuntimeError("API key resolver is not configured")
    return resolver


async def get_current_user(
    request: Request,
    resolver: Annotated[ApiKeyResolver, Depends(get_resolver)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ] = None,
) -> UUID:
    cached = getattr(request.state, "user_id", None)
    if isinstance(cached, UUID):
        return cached
    token = credentials.credentials.strip() if credentials else None
    user_id = resolver.resolve(token)
    if user_id is None:
        raise AuthError()
    request.state.user_id = user_id
    return user_id


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sm = getattr(request.app.state, "sessionmaker", None)
    if sm is None:
        raise RuntimeError("Sessionmaker is not configured")
    return sm


def get_llm(request: Request) -> LLMProvider:
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise RuntimeError("LLM provider is not configured")
    return llm


def get_word_tools_provider(request: Request) -> WordToolsProvider:
    provider = getattr(request.app.state, "word_tools_provider", None)
    if provider is None:
        raise RuntimeError("Word tools provider is not configured")
    return provider


def get_chat_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
    llm: Annotated[LLMProvider, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ChatService:
    return ChatService(sessionmaker, llm, settings)


def get_word_tools_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
    provider: Annotated[WordToolsProvider, Depends(get_word_tools_provider)],
) -> WordToolsService:
    return WordToolsService(sessionmaker, provider)


# --- Music module deps ---


_MAX_EXTERNAL_ID_LEN = 128


async def get_music_user(
    request: Request,
    api_key_user_id: Annotated[UUID, Depends(get_current_user)],
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> MusicUser:
    cached = getattr(request.state, "music_user", None)
    if isinstance(cached, MusicUser):
        return cached
    external_id_raw = request.headers.get("X-User-Id") or request.headers.get(
        "x-user-id"
    )
    external_id = (external_id_raw or "").strip()
    if not external_id:
        raise MissingXUserId(details={"reason": "header_missing"})
    if len(external_id) > _MAX_EXTERNAL_ID_LEN:
        raise MissingXUserId(details={"reason": "header_too_long"})
    async with sessionmaker() as session:
        async with session.begin():
            repo = MusicUsersRepository(session)
            user = await repo.get_or_create(external_id=external_id)
        # detach: keep id/external_id accessible without an active session.
        session.expunge(user)
    request.state.music_user = user
    request.state.music_user_id = user.id
    return user


def get_wallet_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> WalletService:
    return WalletService(sessionmaker)


def get_pricing_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> PricingService:
    return PricingService(sessionmaker)


def get_subscription_gate(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> SubscriptionGate:
    return SubscriptionGate(sessionmaker)
