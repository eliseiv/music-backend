from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Set test env BEFORE importing app modules
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://music:music@localhost:5433/music_test",
)
os.environ.setdefault("API_KEY", "testkey")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("FAL_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("FAL_USE_STUB", "false")
os.environ.setdefault("ADAPTY_WEBHOOK_SECRET", "test-adapty-secret")
os.environ.setdefault("RF_BILLING_WEBHOOK_SECRET", "test-rf-secret")
# disable HEAD-checks by default in tests (avoid network calls)
os.environ.setdefault("MUSIC_URL_CHECK_ENABLED", "false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings():
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def engine(settings) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(
        settings.DATABASE_URL, echo=False, pool_pre_ping=True
    )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _migrate(engine: AsyncEngine):
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as session:
        yield session


@pytest_asyncio.fixture
async def _truncate_music(engine: AsyncEngine):
    """Очистка music-таблиц между тестами. Yields ничего; truncate в teardown.

    Используем CASCADE и включаем все таблицы, чтобы порядок teardown'а
    fixture'ов (seed_pricing, seed_beats, ...) не вызывал FK-конфликтов.
    """
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE job_stage_log, tracks, jobs, token_ledger, "
            "token_wallets, subscription_state, processed_webhooks, "
            "samples, beats, token_products, pricing_rules, "
            "music_users RESTART IDENTITY CASCADE"
        )


@pytest.fixture
def fake_fal():
    from tests.fakes.fake_fal import FakeFal

    return FakeFal()


@pytest_asyncio.fixture
async def app_client(
    settings, engine, fake_fal, _truncate_music
) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def fal_factory(_settings):
        return fake_fal

    app = create_app(
        settings,
        fal_factory=fal_factory,
        sessionmaker=sm,
        engine=engine,
    )

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": "Bearer testkey"},
        ) as client:
            yield client


@pytest.fixture
def auth_headers():
    def _make(user_id: str = "test-user-1") -> dict[str, str]:
        return {
            "Authorization": "Bearer testkey",
            "X-User-Id": user_id,
        }

    return _make
