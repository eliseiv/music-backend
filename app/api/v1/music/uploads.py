from __future__ import annotations

import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.music._common import MUSIC_ERROR_RESPONSES
from app.config import Settings
from app.deps import get_music_user, get_settings_dep
from app.music.models import MusicUser
from app.music.providers.fal.base import FalProvider

router = APIRouter(tags=["Загрузка файлов"])

logger = logging.getLogger(__name__)


class VoiceUploadResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"voiceUrl": "https://fal-cdn/uploaded.wav"}]
        }
    )

    voice_url: str = Field(..., alias="voiceUrl")


def _get_fal_provider(request: Request) -> FalProvider:
    provider = getattr(request.app.state, "fal_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="fal provider is not configured",
        )
    return provider


@router.post(
    "/uploads/voice",
    response_model=VoiceUploadResponse,
    response_model_by_alias=True,
    summary="Загрузить голосовой референс (multipart)",
    description=(
        "**Двухшаговый flow для генерации с голосом:**\n"
        "1. `POST /v1/uploads/voice` → получить `voiceUrl`.\n"
        "2. `POST /v1/tracks/generate` с полем `\"voiceUrl\": \"<полученный url>\"`.\n\n"
        "**Ограничения:**\n"
        "* Размер файла до 25 MiB (`MUSIC_VOICE_MAX_BYTES`)."
    ),
    responses={
        413: {"description": "Файл превышает MUSIC_VOICE_MAX_BYTES"},
        **{
            k: v
            for k, v in MUSIC_ERROR_RESPONSES.items()
            if k in {400, 401, 502, 504}
        },
    },
)
async def upload_voice(
    user: Annotated[MusicUser, Depends(get_music_user)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: Annotated[UploadFile, File()],
    fal: Annotated[FalProvider, Depends(_get_fal_provider)],
) -> VoiceUploadResponse:
    allowed = {
        t.strip().lower()
        for t in settings.MUSIC_VOICE_ALLOWED_CONTENT_TYPES.split(",")
        if t.strip()
    }
    content_type = (file.content_type or "").lower()
    if content_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content-type: {content_type!r}",
        )

    max_bytes = settings.MUSIC_VOICE_MAX_BYTES
    # Stream into memory but cap at limit + 1 byte to detect overshoot.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {max_bytes} bytes",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    filename = file.filename or "voice.bin"
    url = await fal.upload_to_storage(
        content=content, filename=filename, content_type=content_type
    )
    return VoiceUploadResponse(voiceUrl=url)
