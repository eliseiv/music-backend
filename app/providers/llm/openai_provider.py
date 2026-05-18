from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

from app.api.errors import LLMProviderError, LLMTimeout
from app.logging_config import provider_var
from app.providers.llm.base import LLMMessage

logger = logging.getLogger(__name__)


class OpenAIProvider:
    PROVIDER_NAME = "openai"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required")

        effective_base_url = (base_url or "").strip() or self.DEFAULT_BASE_URL
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=effective_base_url,
        )

    async def chat(
        self,
        *,
        messages: Sequence[LLMMessage],
        model: str,
        max_output_tokens: int,
        timeout: float,
    ) -> str:
        token = provider_var.set(self.PROVIDER_NAME)
        try:
            response = await self._call(
                lambda: self._client.chat.completions.create(
                    model=model,
                    messages=list(messages),
                    max_tokens=max_output_tokens,
                    temperature=0.7,
                ),
                timeout=timeout,
                op="chat",
            )
            try:
                content = response.choices[0].message.content or ""
            except (AttributeError, IndexError) as exc:
                raise LLMProviderError("OpenAI returned an empty response") from exc
            return content
        finally:
            provider_var.reset(token)

    async def json_completion(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        token = provider_var.set(self.PROVIDER_NAME)
        try:
            response = await self._call(
                lambda: self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_output_tokens,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                ),
                timeout=timeout,
                op="json_completion",
            )
            try:
                raw = response.choices[0].message.content or ""
            except (AttributeError, IndexError) as exc:
                raise LLMProviderError(
                    "OpenAI returned an empty JSON response"
                ) from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LLMProviderError("OpenAI response is not valid JSON") from exc
            if not isinstance(data, dict):
                raise LLMProviderError("OpenAI JSON response is not an object")
            return data
        finally:
            provider_var.reset(token)

    async def _call(self, factory, *, timeout: float, op: str):
        try:
            return await asyncio.wait_for(factory(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise LLMTimeout() from exc
        except Exception as exc:
            name = exc.__class__.__name__
            if name in {"APITimeoutError", "Timeout"}:
                raise LLMTimeout() from exc
            logger.warning(
                "OpenAI %s call failed: %s: %s", op, name, exc, exc_info=True
            )
            raise LLMProviderError(
                f"OpenAI {op} call failed: {name}: {exc}"
            ) from exc
