from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.errors import TrackNotFound
from app.api.v1.music._common import MUSIC_ERROR_RESPONSES
from app.config import Settings
from app.deps import (
    get_music_user,
    get_pricing_service,
    get_sessionmaker,
    get_settings_dep,
    get_subscription_gate,
    get_wallet_service,
)
from app.music.models import MusicUser
from app.music.providers.fal.base import FalProvider
from app.music.schemas.tracks import (
    GenerateTrackRequest,
    GenerateTrackResponse,
    JobStatusResponse,
    TrackResponse,
)
from app.music.services.generation_service import GenerationService
from app.music.services.pricing_service import PricingService
from app.music.services.subscription_gate import SubscriptionGate
from app.music.services.wallet_service import WalletService

router = APIRouter(tags=["music-tracks"])


def _get_fal_provider(request: Request) -> FalProvider:
    provider = getattr(request.app.state, "fal_provider", None)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="fal provider is not configured (set FAL_API_KEY in .env)",
        )
    return provider


def _get_generation_service(
    sessionmaker: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_sessionmaker)
    ],
    fal: Annotated[FalProvider, Depends(_get_fal_provider)],
    wallet: Annotated[WalletService, Depends(get_wallet_service)],
    pricing: Annotated[PricingService, Depends(get_pricing_service)],
    gate: Annotated[SubscriptionGate, Depends(get_subscription_gate)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> GenerationService:
    return GenerationService(sessionmaker, fal, wallet, pricing, gate, settings)


@router.post(
    "/tracks/generate",
    status_code=status.HTTP_200_OK,
    response_model=GenerateTrackResponse,
    response_model_by_alias=True,
    summary="Создать задание на генерацию трека",
    description=(
        "Запускает асинхронную генерацию музыки через fal.ai. Поэтапно:\n\n"
        "1. Проверка активной подписки (`subscription_inactive` → 402).\n"
        "2. Расчёт стоимости через активный pricing rule и резервирование "
        "токенов (`insufficient_tokens` → 402).\n"
        "3. Отправка в fal.ai с webhook callback.\n"
        "4. Возврат `jobId` (статус `queued/processing`).\n\n"
        "По завершении fal вызовет `POST /webhooks/fal`, и сервис создаст "
        "`tracks` строку. Опросить статус — `GET /tracks/jobs/{jobId}`."
    ),
    responses=MUSIC_ERROR_RESPONSES,
)
async def generate_track(
    body: GenerateTrackRequest,
    user: Annotated[MusicUser, Depends(get_music_user)],
    service: Annotated[GenerationService, Depends(_get_generation_service)],
) -> GenerateTrackResponse:
    payload = body.model_dump(mode="json", by_alias=False)
    payload["beat_id"] = str(body.beat_id)  # JSON-friendly
    result = await service.create_job(
        user_id=user.id,
        request_payload=payload,
        store_stems=body.store_stems,
        desired_duration_seconds=body.desired_duration_seconds,
    )
    return GenerateTrackResponse(
        job_id=result.job_id,
        status=result.status,
        tokens_reserved=result.tokens_reserved,
    )


@router.get(
    "/tracks/jobs/{job_id}",
    response_model=JobStatusResponse,
    response_model_by_alias=True,
    summary="Статус задания на генерацию",
    description="Возвращает текущий статус и stage задания.",
    responses={
        k: v
        for k, v in MUSIC_ERROR_RESPONSES.items()
        if k in {400, 401, 403, 404}
    },
)
async def get_job(
    job_id: UUID,
    user: Annotated[MusicUser, Depends(get_music_user)],
    service: Annotated[GenerationService, Depends(_get_generation_service)],
) -> JobStatusResponse:
    job = await service.get_job(user_id=user.id, job_id=job_id)
    track = await service.get_track_for_job(user_id=user.id, job_id=job_id)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message,
        track_id=track.id if track else None,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


@router.get(
    "/tracks/{track_id}",
    response_model=TrackResponse,
    response_model_by_alias=True,
    summary="Финальный трек",
    description=(
        "Возвращает `audioUrl`, длительность и (если `storeStems=true` при "
        "запросе генерации) карту стемов."
    ),
    responses={
        k: v
        for k, v in MUSIC_ERROR_RESPONSES.items()
        if k in {400, 401, 403, 404}
    },
)
async def get_track(
    track_id: UUID,
    user: Annotated[MusicUser, Depends(get_music_user)],
    service: Annotated[GenerationService, Depends(_get_generation_service)],
) -> TrackResponse:
    track = await service.get_track(user_id=user.id, track_id=track_id)
    return TrackResponse(
        id=track.id,
        job_id=track.job_id,
        audio_url=track.audio_url,
        duration_seconds=float(track.duration_seconds),
        stems=track.stems,
        created_at=track.created_at,
    )
