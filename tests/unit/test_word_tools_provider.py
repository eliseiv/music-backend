from __future__ import annotations

import pytest

from app.api.errors import (
    InvalidQueryForCriterion,
    LLMProviderError,
    ValidationFailed,
)
from app.providers.word_tools.llm_prompt_provider import LLMPromptWordToolsProvider
from app.providers.word_tools.prompt_loader import PromptLoader
from tests.fakes.fake_llm import FakeLLM


@pytest.fixture
def loader(tmp_path):
    from app.providers.word_tools.criteria import CRITERIA_CODES

    shared = tmp_path / "_shared"
    shared.mkdir()
    (shared / "system.txt").write_text("sys", encoding="utf-8")
    for code in CRITERIA_CODES:
        (tmp_path / f"{code}.txt").write_text(
            f"# version: {code}.t\nq={{query}} l={{limit}}", encoding="utf-8"
        )
    loader = PromptLoader(tmp_path)
    loader.load()
    return loader


@pytest.fixture
def provider(loader, settings):
    return LLMPromptWordToolsProvider(llm=FakeLLM(), loader=loader, settings=settings)


async def test_unknown_criterion_raises_validation_failed(provider):
    with pytest.raises(ValidationFailed):
        await provider.search(query="love", criterion="bogus", limit=10, offset=0)


async def test_unscramble_short_query_invalid(provider):
    with pytest.raises(InvalidQueryForCriterion):
        await provider.search(query="a", criterion="unscramble", limit=10, offset=0)


async def test_match_letters_too_long_invalid(provider):
    long_q = "a" * 33
    with pytest.raises(InvalidQueryForCriterion):
        await provider.search(query=long_q, criterion="match_letters", limit=10, offset=0)


async def test_happy_path_returns_items_and_version(loader, settings):
    fake = FakeLLM()
    fake.json_response = {
        "items": [
            {"text": "dove", "score": 0.9},
            {"text": "glove", "score": 0.85},
        ],
        "total": 2,
    }
    p = LLMPromptWordToolsProvider(llm=fake, loader=loader, settings=settings)
    result = await p.search(query="love", criterion="rhymes", limit=10, offset=0)
    assert result.total == 2
    assert [i.text for i in result.items] == ["dove", "glove"]
    assert result.prompt_version == "rhymes.t"


async def test_invalid_json_retries_then_502(loader, settings):
    fake = FakeLLM()
    fake.queue_json_responses(
        [
            {"items": [{"text": "x", "score": 99}], "total": 1},
            {"items": [{"text": "y", "score": 88}], "total": 1},
        ]
    )
    p = LLMPromptWordToolsProvider(llm=fake, loader=loader, settings=settings)
    with pytest.raises(LLMProviderError):
        await p.search(query="love", criterion="rhymes", limit=10, offset=0)
    assert len(fake.json_calls) == 2


async def test_invalid_json_retries_then_succeeds(loader, settings):
    fake = FakeLLM()
    fake.queue_json_responses(
        [
            {"items": [{"text": "x", "score": 99}], "total": 1},
            {"items": [{"text": "dove", "score": 0.9}], "total": 1},
        ]
    )
    p = LLMPromptWordToolsProvider(llm=fake, loader=loader, settings=settings)
    result = await p.search(query="love", criterion="rhymes", limit=10, offset=0)
    assert [i.text for i in result.items] == ["dove"]


async def test_offset_and_limit_slicing(loader, settings):
    fake = FakeLLM()
    fake.json_response = {
        "items": [{"text": f"w{i}", "score": 1.0 - 0.01 * i} for i in range(10)],
        "total": 10,
    }
    p = LLMPromptWordToolsProvider(llm=fake, loader=loader, settings=settings)
    result = await p.search(query="love", criterion="rhymes", limit=3, offset=2)
    assert [i.text for i in result.items] == ["w2", "w3", "w4"]
    assert result.total == 10
