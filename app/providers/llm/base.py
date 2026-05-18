from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypedDict


class LLMMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMProvider(Protocol):
    async def chat(
        self,
        *,
        messages: Sequence[LLMMessage],
        model: str,
        max_output_tokens: int,
        timeout: float,
    ) -> str:
        ...

    async def json_completion(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        ...
