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
from app.music.services.recovery import recover_orphan_jobs, report_received_webhooks
from app.music.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


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

        fal = (fal_factory or _default_fal_factory)(settings)
        app.state.fal_provider = fal

        try:
            recovered = await recover_orphan_jobs(
                sessionmaker=app.state.sessionmaker,
                wallet=WalletService(app.state.sessionmaker),
            )
            if recovered:
                logger.info("Recovered %d orphan jobs on startup", recovered)
            stuck = await report_received_webhooks(
                sessionmaker=app.state.sessionmaker
            )
            if stuck:
                logger.warning(
                    "Found %d webhooks stuck in 'received' (manual review needed)",
                    stuck,
                )
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
        title="Music Generation API",
        summary="Backend генерации музыки через fal.ai с токенами и подписками",
        description=(
            "## О сервисе\n\n"
            "Backend для генерации музыки через **fal.ai** с монетизацией "
            "на токенах и подписками (Adapty / RuStore). Реализует все "
            "разделы технического задания (§1–§15).\n\n"
            "**Базовый префикс всех эндпоинтов:** `/v1/...`\n\n"
            "---\n\n"
            "## Авторизация\n\n"
            "Все запросы под `/v1/` (кроме webhook'ов) требуют **два заголовка**:\n\n"
            "```\n"
            "Authorization: Bearer <API_KEY>\n"
            "X-User-Id: <стабильный идентификатор клиента>\n"
            "```\n\n"
            "* **`API_KEY`** — статический Bearer-ключ, зашитый в мобильном клиенте.\n"
            "* **`X-User-Id`** — стабильный идентификатор устройства/профиля "
            "(например, Adapty profile id). По нему backend ведёт записи в БД.\n\n"
            "Webhook'и (`/v1/webhooks/...`) **не используют** Bearer — они "
            "авторизуются подписью провайдера (HMAC-SHA256 или Bearer-секретом).\n\n"
            "**В Swagger UI** нажмите кнопку **Authorize** 🔓 в правом верхнем "
            "углу и введите ваш `API_KEY`. Заголовок `X-User-Id` для каждого "
            "запроса вводится отдельно (в секции *Parameters*).\n\n"
            "---\n\n"
            "## Формат ошибок\n\n"
            "Все 4xx/5xx ответы возвращают единый envelope (ТЗ §13):\n\n"
            "```json\n"
            "{\n"
            "  \"error\": {\n"
            "    \"code\": \"INSUFFICIENT_TOKENS\",\n"
            "    \"message\": \"Not enough tokens to perform the operation\",\n"
            "    \"details\": { \"required\": 2, \"available\": 0 }\n"
            "  },\n"
            "  \"requestId\": \"b5830b11dc4747d4b6b85217eff10177\"\n"
            "}\n"
            "```\n\n"
            "Коды (UPPER_SNAKE_CASE): `INVALID_INPUT`, `INVALID_SAMPLE_URL`, "
            "`MISSING_X_USER_ID`, `UNAUTHORIZED`, `SUBSCRIPTION_REQUIRED`, "
            "`SUBSCRIPTION_EXPIRED`, `INSUFFICIENT_TOKENS`, `FORBIDDEN`, "
            "`BEAT_NOT_FOUND`, `JOB_NOT_FOUND`, `TRACK_NOT_FOUND`, "
            "`WEBHOOK_SIGNATURE_INVALID`, `WEBHOOK_PAYLOAD_INVALID`, "
            "`PROVIDER_FAILED`, `PROVIDER_TIMEOUT`, `INTERNAL_ERROR`, "
            "`RATE_LIMITED`.\n\n"
            "Заголовок `X-Request-Id` дублируется во всех ответах — для "
            "поиска в логах. Можно прислать свой `X-Request-Id` в запросе.\n\n"
            "---\n\n"
            "## Сценарий генерации трека\n\n"
            "1. **`POST /v1/uploads/voice`** *(опционально)* — загрузить файл "
            "голоса, получить `voiceUrl`.\n"
            "2. **`GET /v1/beats`** — выбрать бит из 5 жанров.\n"
            "3. **`GET /v1/samples`** — собрать набор sample-элементов (10 категорий).\n"
            "4. **`POST /v1/tracks/generate`** — запустить генерацию. Сервис проверит "
            "активность подписки, доступность URL, резервирует токены и вернёт `jobId`.\n"
            "5. **`GET /v1/tracks/jobs/{jobId}`** — опрашивать статус, "
            "получая `pipeline` со списком стадий.\n"
            "6. **`GET /v1/tracks/{trackId}`** — забрать готовый трек "
            "(`audioUrl`, длительность, опционально `stems`).\n\n"
            "---\n\n"
            "## Webhook'и\n\n"
            "* `POST /v1/webhooks/fal` — fal.ai сообщает о завершении стадии "
            "пайплайна (HMAC-SHA256 в `X-Fal-Signature`).\n"
            "* `POST /v1/webhooks/billing/adapty` — Adapty присылает события "
            "подписки/покупки (Bearer-секрет в `Authorization`).\n"
            "* `POST /v1/webhooks/billing/rf` — RuStore присылает события "
            "(HMAC-SHA256 в `X-RuStore-Signature`).\n"
        ),
        version="1.0.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "Генерация треков",
                "description": (
                    "Запуск и контроль генерации музыки через fal.ai. "
                    "Полный пайплайн из 8 стадий: `prepare_prompt → lyrics → "
                    "music_generation → audio_to_audio_refine → vocal_tts → "
                    "mix_master → upload_cdn → finalize` (ТЗ §11).\n\n"
                    "Требует **активной подписки** и **достаточного баланса "
                    "токенов** (gate-проверка перед резервом)."
                ),
            },
            {
                "name": "Каталог",
                "description": (
                    "Каталог битов (5 жанров) и sound-элементов (10 категорий "
                    "с тегами): bass / lead / chord / kick / snare / "
                    "closed_hi_hat / open_hi_hat / auxiliary / mixing / "
                    "sound_effects.\n\n"
                    "URL'ы в ответах пригодны для демо-воспроизведения "
                    "на устройстве (ТЗ §9.2)."
                ),
            },
            {
                "name": "Баланс и тарифы",
                "description": (
                    "Текущий баланс токенов пользователя (`available` / "
                    "`reserved` / `frozen`) и каталог токен-паков для покупки "
                    "через Adapty/RuStore."
                ),
            },
            {
                "name": "Загрузка файлов",
                "description": (
                    "Загрузка голосового референса (multipart) в fal "
                    "storage. Возвращает `voiceUrl`, который потом "
                    "подставляется в `POST /v1/tracks/generate`."
                ),
            },
            {
                "name": "Webhooks",
                "description": (
                    "Эндпоинты для входящих webhook'ов от внешних провайдеров: "
                    "fal.ai (завершение стадий генерации), Adapty (события "
                    "App Store / Google Play подписок), RuStore (РФ биллинг). "
                    "Авторизуются подписью провайдера, **Bearer не нужен**."
                ),
            },
            {
                "name": "Система",
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
        tags=["Система"],
        summary="Healthcheck",
        description=(
            "Проверка живости сервиса. Не требует авторизации. "
            "Используется в docker-compose healthcheck и в smoke-тестах CI."
        ),
    )
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
