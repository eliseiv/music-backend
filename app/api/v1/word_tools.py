from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.deps import get_current_user, get_word_tools_service
from app.providers.word_tools.criteria import CRITERIA
from app.schemas.common import ErrorResponse
from app.schemas.word_tools import (
    CriteriaResponse,
    CriterionItem,
    WordToolsItemSchema,
    WordToolsSearchRequest,
    WordToolsSearchResponse,
)
from app.services.word_tools_service import WordToolsService

router = APIRouter(tags=["word-tools"])

_ERROR_RESPONSES = {
    400: {
        "model": ErrorResponse,
        "description": "Ошибка валидации (неизвестный criterion, пустой query)",
    },
    401: {"model": ErrorResponse, "description": "Нет или неверный API-ключ"},
    422: {
        "model": ErrorResponse,
        "description": (
            "Query не подходит под выбранный criterion "
            "(например, слишком короткий)"
        ),
    },
    429: {"model": ErrorResponse, "description": "Превышен лимит запросов"},
    502: {
        "model": ErrorResponse,
        "description": "Ошибка LLM-провайдера или невалидный JSON в ответе",
    },
    504: {"model": ErrorResponse, "description": "Таймаут LLM-провайдера"},
}


@router.get(
    "/word-tools/criteria",
    status_code=status.HTTP_200_OK,
    response_model=CriteriaResponse,
    response_model_by_alias=True,
    responses={401: _ERROR_RESPONSES[401]},
    summary="Список поддерживаемых критериев",
    description=(
        "Возвращает все 16 критериев, которые поддерживает "
        "`POST /word-tools/search`. Удобно использовать для "
        "построения dropdown-выбора в UI."
    ),
)
async def list_criteria(
    user_id: Annotated[UUID, Depends(get_current_user)],
) -> CriteriaResponse:
    return CriteriaResponse(
        criteria=[CriterionItem(code=code, title=title) for code, title in CRITERIA]
    )


@router.post(
    "/word-tools/search",
    status_code=status.HTTP_200_OK,
    response_model=WordToolsSearchResponse,
    response_model_by_alias=True,
    responses=_ERROR_RESPONSES,
    summary="Поиск слов и фраз по языковому критерию",
    description=(
        "Ищет английские слова, фразы или анаграммы по выбранному "
        "критерию через LLM (OpenAI).\n\n"
        "**Поддерживаемые критерии:** `rhymes`, `rhymes_advanced`, "
        "`near_rhymes`, `synonyms`, `descriptive_words`, `phrases`, "
        "`antonyms`, `definitions`, `related_words`, `similar_sounding`, "
        "`similarly_spelled`, `homophones`, `phrase_rhymes`, "
        "`match_consonants`, `match_letters`, `unscramble`.\n\n"
        "**Особенности валидации:**\n"
        "- `unscramble` и `match_consonants` требуют не менее 2 "
        "буквенных символов — иначе `422`.\n"
        "- `match_letters` принимает паттерн до 32 символов с поддержкой "
        "wildcards `?` (один символ) и `*` (любая последовательность).\n\n"
        "Все результаты возвращаются на английском. Поле "
        "`promptVersion` отражает версию использованного шаблона."
    ),
)
async def search_words(
    body: WordToolsSearchRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    service: Annotated[WordToolsService, Depends(get_word_tools_service)],
) -> WordToolsSearchResponse:
    result = await service.search(
        user_id=user_id,
        query=body.query,
        criterion=body.criterion,
        limit=body.limit,
        offset=body.offset,
    )
    return WordToolsSearchResponse(
        query=result.query,
        criterion=result.criterion,
        total=result.total,
        items=[WordToolsItemSchema(text=i.text, score=i.score) for i in result.items],
        prompt_version=result.prompt_version,
    )
