from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from app.providers.word_tools.criteria import CriterionCode
from app.schemas.common import CamelModel


class CriterionItem(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [{"code": "rhymes", "title": "Rhymes"}]
        },
    )

    code: str = Field(
        description="Машинно-читаемый код критерия (rhymes, synonyms, ...).",
        examples=["rhymes"],
    )
    title: str = Field(
        description="Человеко-читаемое название критерия для UI.",
        examples=["Rhymes"],
    )


class CriteriaResponse(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "criteria": [
                        {"code": "rhymes", "title": "Rhymes"},
                        {"code": "synonyms", "title": "Synonyms"},
                        {"code": "antonyms", "title": "Antonyms"},
                        {"code": "definitions", "title": "Definitions"},
                    ]
                }
            ]
        },
    )

    criteria: list[CriterionItem] = Field(
        description="Список из 16 поддерживаемых критериев в порядке ТЗ.",
    )


class WordToolsSearchRequest(CamelModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "query": "love",
                    "criterion": "rhymes",
                    "limit": 10,
                    "offset": 0,
                },
                {
                    "query": "happy",
                    "criterion": "synonyms",
                    "limit": 10,
                    "offset": 0,
                },
                {
                    "query": "listen",
                    "criterion": "unscramble",
                    "limit": 15,
                    "offset": 0,
                },
            ]
        },
    )

    query: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Слово, фраза или буквенный паттерн на английском "
            "(1..120 символов). Для каждого критерия могут быть "
            "дополнительные требования — см. ошибку 422."
        ),
        examples=["love"],
    )
    criterion: CriterionCode = Field(
        description=(
            "Код критерия из `GET /word-tools/criteria`. "
            "Один из 16 поддерживаемых значений."
        ),
        examples=["rhymes"],
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Сколько результатов вернуть (1..200).",
        examples=[10],
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Сдвиг для пагинации (≥0).",
        examples=[0],
    )

    @field_validator("query", mode="before")
    @classmethod
    def _strip_query(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


class WordToolsItemSchema(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [{"text": "dove", "score": 0.98}]
        },
    )

    text: str = Field(
        description="Найденное слово или фраза (на английском).",
        examples=["dove"],
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Релевантность 0..1 (1 — максимум).",
        examples=[0.98],
    )


class WordToolsSearchResponse(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "query": "love",
                    "criterion": "rhymes",
                    "total": 5,
                    "items": [
                        {"text": "dove", "score": 1.0},
                        {"text": "glove", "score": 1.0},
                        {"text": "shove", "score": 1.0},
                        {"text": "above", "score": 0.8},
                        {"text": "of", "score": 0.5},
                    ],
                    "promptVersion": "rhymes.v1",
                }
            ]
        },
    )

    query: str = Field(description="Исходный query (после нормализации).")
    criterion: str = Field(description="Применённый критерий.")
    total: int = Field(
        description="Общее число результатов до пагинации.",
        examples=[5],
    )
    items: list[WordToolsItemSchema] = Field(
        description="Результаты после применения `limit`/`offset`.",
    )
    prompt_version: str | None = Field(
        default=None,
        description=(
            "Версия использованного шаблона промпта "
            "(из `# version:` директивы или sha256-fallback)."
        ),
        examples=["rhymes.v1"],
    )
