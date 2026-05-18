from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.music._common import MUSIC_ERROR_RESPONSES
from app.deps import get_music_user
from app.music.enums import BeatGenre
from app.music.models import MusicUser
from app.music.schemas.beats import BeatItem, BeatsResponse
from app.music.services.catalog_service import CatalogService
from app.music.services.catalog_service import CatalogService as _CS  # alias for Depends
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.deps import get_sessionmaker

router = APIRouter(tags=["Каталог"])


def _get_catalog_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
) -> CatalogService:
    return CatalogService(sessionmaker)


@router.get(
    "/beats",
    response_model=BeatsResponse,
    response_model_by_alias=True,
    summary="Каталог битов (5 жанров)",
    description=(
        "Возвращает активные биты, опционально отфильтрованные по жанру.\n\n"
        "**Жанры** (ТЗ дизайн):\n"
        "* `electronic_dance` — Electronic Dance\n"
        "* `rap` — Rap Beats\n"
        "* `lofi` — Lo-Fi\n"
        "* `global_groove` — Global Groove\n"
        "* `relaxing_meditation` — Relaxing Meditation\n\n"
        "Поле `audioUrl` пригодно для демо-воспроизведения на устройстве."
    ),
    responses={
        k: v for k, v in MUSIC_ERROR_RESPONSES.items() if k in {400, 401}
    },
)
async def list_beats(
    user: Annotated[MusicUser, Depends(get_music_user)],
    catalog: Annotated[CatalogService, Depends(_get_catalog_service)],
    genre: Annotated[
        BeatGenre | None,
        Query(description="Фильтр по жанру (опционально)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Сколько вернуть.")] = 200,
    offset: Annotated[int, Query(ge=0, description="Сдвиг для пагинации.")] = 0,
) -> BeatsResponse:
    beats = await catalog.list_beats(genre=genre, limit=limit, offset=offset)
    return BeatsResponse(
        beats=[BeatItem.model_validate(b) for b in beats]
    )
