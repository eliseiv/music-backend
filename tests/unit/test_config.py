from __future__ import annotations

from uuid import UUID

from app.config import Settings


def _base_kwargs(**overrides):
    base = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
    }
    base.update(overrides)
    return base


def test_no_api_key_results_in_empty_map():
    s = Settings(**_base_kwargs())
    assert s.api_key_map == {}
    assert s.api_user_id is None


def test_empty_api_key_treated_as_none():
    s = Settings(**_base_kwargs(API_KEY=""))
    assert s.api_key_map == {}
    assert s.api_user_id is None


def test_single_api_key_derives_uuid():
    s = Settings(**_base_kwargs(API_KEY="my-secret"))
    mapping = s.api_key_map
    assert list(mapping.keys()) == ["my-secret"]
    assert isinstance(mapping["my-secret"], UUID)
    s2 = Settings(**_base_kwargs(API_KEY="my-secret"))
    assert s.api_key_map == s2.api_key_map
    assert s.api_user_id == s2.api_user_id


def test_different_api_keys_produce_different_user_ids():
    s_a = Settings(**_base_kwargs(API_KEY="alpha"))
    s_b = Settings(**_base_kwargs(API_KEY="beta"))
    assert s_a.api_user_id != s_b.api_user_id
