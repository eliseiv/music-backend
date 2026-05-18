from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_API_KEY_NAMESPACE = uuid.UUID("6f9ea6e8-5d3c-4d2b-9b6e-2e5b4f4a7d10")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_ENV: Literal["dev", "prod", "test"] = "dev"
    LOG_LEVEL: str = "INFO"
    HTTP_HOST: str = "0.0.0.0"
    HTTP_PORT: int = 8000

    DATABASE_URL: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    API_KEY: str | None = None

    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_BASE_URL: str | None = None
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    OPENAI_WORDTOOLS_MODEL: str = "gpt-4o-mini"
    LLM_CHAT_TIMEOUT_SECONDS: float = 20.0
    LLM_WORDTOOLS_TIMEOUT_SECONDS: float = 8.0
    LLM_MAX_INPUT_TOKENS: int = 6000
    LLM_MAX_OUTPUT_TOKENS: int = 1024

    MAX_MESSAGE_CHARS: int = 8000
    HISTORY_MAX_MESSAGES: int = 30
    CHAT_SYSTEM_PROMPT: str = "You are a helpful assistant."

    WORD_TOOLS_PROMPTS_DIR: Path = Path("prompts")
    WORD_TOOLS_DEFAULT_LIMIT: int = 50
    WORD_TOOLS_MAX_LIMIT: int = 200

    RATE_LIMIT_PER_MINUTE: int = 0
    RATE_LIMIT_BURST: int = 60

    # --- Music module ---
    PUBLIC_BASE_URL: str = ""

    FAL_API_KEY: SecretStr = SecretStr("")
    FAL_BASE_URL: str = "https://queue.fal.run"
    FAL_HTTP_TIMEOUT_SECONDS: float = 30.0
    FAL_WEBHOOK_SECRET: SecretStr = SecretStr("")
    FAL_USE_STUB: bool = False  # dev-only: подменить fal на in-process stub
    FAL_MUSIC_MODEL: str = "fal-ai/minimax-music"
    FAL_REFINE_MODEL: str = "fal-ai/ace-step/audio-to-audio"
    FAL_SPEECH_MODEL: str = "fal-ai/minimax/speech-02-turbo"

    ADAPTY_WEBHOOK_SECRET: SecretStr = SecretStr("")
    RF_BILLING_WEBHOOK_SECRET: SecretStr = SecretStr("")

    MUSIC_MAX_CONCURRENT_GENERATIONS: int = 8
    MUSIC_VOICE_MAX_BYTES: int = 26_214_400  # 25 MiB
    MUSIC_VOICE_ALLOWED_CONTENT_TYPES: str = (
        "audio/mpeg,audio/wav,audio/mp4,audio/x-m4a"
    )
    MUSIC_VOICE_MAX_CONCURRENT_UPLOADS: int = 4
    MUSIC_DEFAULT_TRACK_DURATION_SECONDS: int = 60
    MUSIC_JOB_HARD_TIMEOUT_SECONDS: int = 1800

    @field_validator("OPENAI_BASE_URL", "API_KEY", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_user_id(self) -> UUID | None:
        if not self.API_KEY:
            return None
        return uuid.uuid5(_API_KEY_NAMESPACE, self.API_KEY)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_key_map(self) -> dict[str, UUID]:
        if not self.API_KEY:
            return {}
        user_id = self.api_user_id
        assert user_id is not None
        return {self.API_KEY: user_id}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
