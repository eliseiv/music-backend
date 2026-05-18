from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.api.errors import (
    InvalidQueryForCriterion,
    LLMProviderError,
    ValidationFailed,
)
from app.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.word_tools.base import (
    WordToolsItem,
    WordToolsProvider,
    WordToolsResult,
)
from app.providers.word_tools.criteria import CRITERIA_CODES
from app.providers.word_tools.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


class _RawItem(BaseModel):
    text: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)


class _RawResponse(BaseModel):
    items: list[_RawItem] = Field(default_factory=list)
    total: int | None = None


_RETRY_NUDGE = (
    "\n\nIMPORTANT: Output ONLY valid JSON of shape "
    '{"items":[{"text":"...","score":0.0..1.0}],"total":<int>}. '
    "No prose, no markdown."
)


def _validate_query_for_criterion(criterion: str, query: str) -> None:
    q = query.strip()
    if not q:
        raise ValidationFailed(
            "query must not be empty", details={"field": "query"}
        )
    if criterion in {"unscramble", "match_consonants"}:
        if len(q) < 2 or not q.isalpha():
            raise InvalidQueryForCriterion(
                f"Criterion {criterion!r} requires at least 2 alphabetic characters",
                details={"criterion": criterion, "query": q},
            )
    if criterion == "match_letters":
        if len(q) < 1 or len(q) > 32:
            raise InvalidQueryForCriterion(
                "match_letters pattern must be 1..32 characters long",
                details={"criterion": criterion, "query": q},
            )
    if len(q) > 120:
        raise InvalidQueryForCriterion(
            "query is too long for word-tools search",
            details={"criterion": criterion, "max_chars": 120},
        )


class LLMPromptWordToolsProvider(WordToolsProvider):
    def __init__(
        self,
        *,
        llm: LLMProvider,
        loader: PromptLoader,
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._loader = loader
        self._settings = settings

    async def search(
        self,
        *,
        query: str,
        criterion: str,
        limit: int,
        offset: int,
    ) -> WordToolsResult:
        if criterion not in CRITERIA_CODES:
            raise ValidationFailed(
                f"Unknown criterion: {criterion!r}",
                details={"field": "criterion", "value": criterion},
            )
        _validate_query_for_criterion(criterion, query)

        template = self._loader.get(criterion)
        fetch_limit = min(self._settings.WORD_TOOLS_MAX_LIMIT, max(limit + offset, limit))
        rendered = template.render(query=query, limit=fetch_limit)
        system = self._loader.shared_system

        try:
            raw = await self._call_llm(system=system, user=rendered)
        except LLMProviderError:
            raw = await self._call_llm(system=system, user=rendered + _RETRY_NUDGE)

        all_items = [WordToolsItem(text=i.text, score=i.score) for i in raw.items]
        total = raw.total if raw.total is not None else len(all_items)
        page = all_items[offset : offset + limit]
        return WordToolsResult(
            items=page,
            total=total,
            prompt_version=template.version,
        )

    async def _call_llm(self, *, system: str, user: str) -> _RawResponse:
        data: dict[str, Any] = await self._llm.json_completion(
            system=system,
            user=user,
            model=self._settings.OPENAI_WORDTOOLS_MODEL,
            max_output_tokens=self._settings.LLM_MAX_OUTPUT_TOKENS,
            timeout=self._settings.LLM_WORDTOOLS_TIMEOUT_SECONDS,
        )
        try:
            return _RawResponse.model_validate(data)
        except ValidationError as exc:
            raise LLMProviderError(
                "LLM JSON response did not match the expected schema",
                details={"errors": exc.errors()[:5]},
            ) from exc
