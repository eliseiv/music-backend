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
    "postgresql+asyncpg://aibased:aibased@localhost:5432/aibased_test",
)
os.environ.setdefault("API_KEY", "testkey")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings():
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def engine(settings) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
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
async def _truncate(engine: AsyncEngine):
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE messages, conversations, search_requests "
            "RESTART IDENTITY CASCADE"
        )


@pytest.fixture
def fake_llm():
    from tests.fakes.fake_llm import FakeLLM

    return FakeLLM()


@pytest_asyncio.fixture
async def app_client(
    settings, engine, fake_llm, _truncate
) -> AsyncIterator[AsyncClient]:
    from app.main import create_app
    from app.providers.word_tools.llm_prompt_provider import LLMPromptWordToolsProvider

    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def llm_factory(_settings):
        return fake_llm

    def wt_factory(_settings, _llm, loader):
        return LLMPromptWordToolsProvider(
            llm=fake_llm, loader=loader, settings=_settings
        )

    app = create_app(
        settings,
        llm_factory=llm_factory,
        word_tools_provider_factory=wt_factory,
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
    def _make() -> dict[str, str]:
        return {"Authorization": "Bearer testkey"}

    return _make
