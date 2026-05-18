from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_v1_router
from app.auth.api_keys import ApiKeyResolver
from app.config import Settings, get_settings
from app.db.session import build_engine, build_sessionmaker
from app.logging_config import setup_logging
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.music.providers.fal.client import FalAiProvider
from app.music.providers.fal.stub import StubFalProvider
from app.music.services.recovery import recover_orphan_jobs
from app.music.services.wallet_service import WalletService
from app.providers.llm.openai_provider import OpenAIProvider
from app.providers.word_tools.llm_prompt_provider import LLMPromptWordToolsProvider
from app.providers.word_tools.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


def _default_llm_factory(settings: Settings):
    return OpenAIProvider(
        api_key=settings.OPENAI_API_KEY.get_secret_value(),
        base_url=settings.OPENAI_BASE_URL,
    )


def _default_fal_factory(settings: Settings):
    if settings.FAL_USE_STUB:
        logger.warning(
            "FAL_USE_STUB=true — using in-process StubFalProvider (dev only)"
        )
        return StubFalProvider(
            webhook_secret=settings.FAL_WEBHOOK_SECRET.get_secret_value()
        )
    key = settings.FAL_API_KEY.get_secret_value()
    if not key:
        logger.warning(
            "FAL_API_KEY is not configured; music-generation endpoints will 503"
        )
        return None
    return FalAiProvider(
        api_key=key,
        base_url=settings.FAL_BASE_URL,
        music_model=settings.FAL_MUSIC_MODEL,
        refine_model=settings.FAL_REFINE_MODEL,
        speech_model=settings.FAL_SPEECH_MODEL,
        webhook_secret=settings.FAL_WEBHOOK_SECRET.get_secret_value(),
        timeout_seconds=settings.FAL_HTTP_TIMEOUT_SECONDS,
    )


def create_app(
    settings: Settings | None = None,
    *,
    llm_factory=None,
    word_tools_provider_factory=None,
    fal_factory=None,
    sessionmaker=None,
    engine=None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if engine is None and sessionmaker is None:
            local_engine = build_engine(settings)
            local_sessionmaker = build_sessionmaker(local_engine)
            app.state.engine = local_engine
            app.state.sessionmaker = local_sessionmaker
        else:
            if engine is not None:
                app.state.engine = engine
            if sessionmaker is not None:
                app.state.sessionmaker = sessionmaker

        loader = PromptLoader(settings.WORD_TOOLS_PROMPTS_DIR)
        loader.load()
        app.state.prompt_loader = loader

        llm = (llm_factory or _default_llm_factory)(settings)
        app.state.llm = llm

        if word_tools_provider_factory is not None:
            provider = word_tools_provider_factory(settings, llm, loader)
        else:
            provider = LLMPromptWordToolsProvider(
                llm=llm, loader=loader, settings=settings
            )
        app.state.word_tools_provider = provider

        # Music: fal provider + recovery sweep
        fal = (fal_factory or _default_fal_factory)(settings)
        app.state.fal_provider = fal

        try:
            recovered = await recover_orphan_jobs(
                sessionmaker=app.state.sessionmaker,
                wallet=WalletService(app.state.sessionmaker),
            )
            if recovered:
                logger.info("Recovered %d orphan jobs on startup", recovered)
        except Exception:
            logger.exception("Recovery sweep failed on startup")

        try:
            yield
        finally:
            fal_instance = getattr(app.state, "fal_provider", None)
            if fal_instance is not None and hasattr(fal_instance, "aclose"):
                try:
                    await fal_instance.aclose()
                except Exception:
                    logger.exception("Failed to close fal provider")
            local_engine = getattr(app.state, "engine", None)
            if local_engine is not None and engine is None:
                await local_engine.dispose()

    app = FastAPI(
        title="AI Backend — AI Chat, Word Tools, Music Generation",
        description=(
            "Backend для AI-чата, поиска слов и генерации музыки через "
            "fal.ai с монетизацией на токенах.\n\n"
            "**Авторизация:** на каждом запросе (кроме `/healthz` и "
            "webhook-эндпоинтов) передавайте `Authorization: Bearer <API_KEY>`. "
            "Для music-эндпоинтов дополнительно нужен заголовок "
            "`X-User-Id` (Adapty profile id или аналогичный устойчивый "
            "идентификатор устройства).\n\n"
            "В Swagger UI нажмите кнопку **Authorize** в правом верхнем углу "
            "и введите ваш `API_KEY`."
        ),
        version="1.0.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "chat",
                "description": (
                    "AI-чат: создание conversation и обмен сообщениями "
                    "с учётом истории."
                ),
            },
            {
                "name": "word-tools",
                "description": (
                    "Поиск слов и фраз по 16 языковым критериям "
                    "(рифмы, синонимы, антонимы и т. д.). Все запросы "
                    "выполняются на английском языке."
                ),
            },
            {
                "name": "music-catalog",
                "description": (
                    "Каталог битов и sound-элементов. Требует `X-User-Id`."
                ),
            },
            {
                "name": "music-tracks",
                "description": (
                    "Генерация треков через fal.ai (gate → reserve → submit → "
                    "webhook → capture). Требует `X-User-Id` и активной "
                    "подписки."
                ),
            },
            {
                "name": "music-tokens",
                "description": (
                    "Баланс токенов и каталог токен-паков."
                ),
            },
            {
                "name": "music-uploads",
                "description": (
                    "Загрузка пользовательских ресурсов (voice) в fal storage."
                ),
            },
            {
                "name": "music-webhooks",
                "description": (
                    "Webhook-эндпоинты для fal.ai, Adapty и RuStore. "
                    "Авторизуются подписью провайдера, не через Bearer."
                ),
            },
            {
                "name": "system",
                "description": "Служебные эндпоинты (healthcheck).",
            },
        ],
    )

    app.state.settings = settings
    app.state.api_key_resolver = ApiKeyResolver(settings.api_key_map)

    register_exception_handlers(app)
    if settings.RATE_LIMIT_PER_MINUTE > 0:
        app.add_middleware(
            RateLimitMiddleware,
            resolver=app.state.api_key_resolver,
            per_minute=settings.RATE_LIMIT_PER_MINUTE,
            burst=max(settings.RATE_LIMIT_BURST, 1),
        )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_v1_router)

    @app.get(
        "/healthz",
        tags=["system"],
        summary="Healthcheck",
        description="Проверка живости сервиса. Не требует авторизации.",
    )
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
