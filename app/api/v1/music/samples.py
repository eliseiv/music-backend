from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.music._common import MUSIC_ERROR_RESPONSES
from app.deps import get_music_user, get_sessionmaker
from app.music.enums import SampleCategory
from app.music.models import MusicUser
from app.music.schemas.samples import (
    SamplesByCategoryResponse,
    group_samples_by_category,
)
from app.music.services.catalog_service import CatalogService

router = APIRouter(tags=["Каталог"])


def _get_catalog_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> CatalogService:
    return CatalogService(sessionmaker)


@router.get(
    "/samples",
    response_model=SamplesByCategoryResponse,
    response_model_by_alias=True,
    summary="Sound-элементы по 10 категориям",
    description=(
        "Возвращает все активные sound-элементы, сгруппированные по 10 "
        "категориям (ТЗ дизайн):\n\n"
        "**Harmonic** (instrument groups, с тегами):\n"
        "* `bass` — Bass\n"
        "* `lead` — Lead\n"
        "* `chord` — Chord\n\n"
        "**Drums** (drum kits, с тегами):\n"
        "* `kick` — Kick\n"
        "* `snare` — Snare\n"
        "* `closed_hi_hat` — Closed hi-hat\n"
        "* `open_hi_hat` — Open hi-hat\n"
        "* `auxiliary` — Auxiliary\n\n"
        "**Без тегов** (категории общего назначения):\n"
        "* `mixing` — Mixing\n"
        "* `sound_effects` — Sound effects\n\n"
        "### Теги Harmonic (14)\n"
        "`all_instruments`, `acoustic_guitars`, `global_ensemble`, "
        "`acoustic_instruments`, `chill_keys`, `seventies_fusion`, "
        "`jazz_trio`, `rock_n_roll`, `soft_rock`, `classical_strings`, "
        "`synth_haven`, `smooth_pop`, `carolina_trap_set`, `brass_and_winds`.\n\n"
        "### Теги Drums (7)\n"
        "`all_drums`, `acoustic`, `dusty`, `edm`, `experimental`, `trap_808`, "
        "`vintage_electronic`.\n\n"
        "Опциональные фильтры `category` и `tag` сужают выдачу."
    ),
    responses={
        k: v for k, v in MUSIC_ERROR_RESPONSES.items() if k in {400, 401}
    },
)
async def list_samples(
    user: Annotated[MusicUser, Depends(get_music_user)],
    catalog: Annotated[CatalogService, Depends(_get_catalog_service)],
    category: Annotated[
        SampleCategory | None,
        Query(description="Фильтр по категории (опционально)."),
    ] = None,
    tag: Annotated[
        str | None,
        Query(min_length=1, max_length=64, description="Фильтр по тегу (опционально)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SamplesByCategoryResponse:
    samples = await catalog.list_samples(
        category=category, tag=tag, limit=limit, offset=offset
    )
    return SamplesByCategoryResponse(
        categories=group_samples_by_category(samples)
    )
