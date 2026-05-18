from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class WordToolsItem:
    text: str
    score: float


@dataclass
class WordToolsResult:
    items: list[WordToolsItem]
    total: int
    prompt_version: str | None = None


class WordToolsProvider(Protocol):
    async def search(
        self,
        *,
        query: str,
        criterion: str,
        limit: int,
        offset: int,
    ) -> WordToolsResult:
        ...
