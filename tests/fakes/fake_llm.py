from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.providers.llm.base import LLMMessage


class FakeLLM:
    def __init__(self) -> None:
        self.chat_response: str | Exception = "fake-assistant-response"
        self.json_response: dict[str, Any] | Exception = {
            "items": [{"text": "dove", "score": 0.9}],
            "total": 1,
        }
        self.chat_calls: list[dict[str, Any]] = []
        self.json_calls: list[dict[str, Any]] = []
        self._json_responses_queue: list[dict[str, Any] | Exception] = []

    def queue_json_responses(self, responses: list[dict[str, Any] | Exception]) -> None:
        self._json_responses_queue = list(responses)

    async def chat(
        self,
        *,
        messages: Sequence[LLMMessage],
        model: str,
        max_output_tokens: int,
        timeout: float,
    ) -> str:
        self.chat_calls.append(
            {
                "messages": list(messages),
                "model": model,
                "max_output_tokens": max_output_tokens,
                "timeout": timeout,
            }
        )
        if isinstance(self.chat_response, Exception):
            raise self.chat_response
        return self.chat_response

    async def json_completion(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_output_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        self.json_calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "max_output_tokens": max_output_tokens,
                "timeout": timeout,
            }
        )
        if self._json_responses_queue:
            response = self._json_responses_queue.pop(0)
        else:
            response = self.json_response
        if isinstance(response, Exception):
            raise response
        return response
