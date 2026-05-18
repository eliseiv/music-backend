from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
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
    StageEntry,
    TrackResponse,
)
from app.music.services.generation_service import GenerationService
from app.music.services.pricing_service import PricingService
from app.music.services.subscription_gate import SubscriptionGate
from app.music.services.wallet_service import WalletService

router = APIRouter(tags=["Генерация треков"])


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
    summary="Запустить генерацию трека",
    description=(
        "### Idempotency-Key\n\n"
        "Опциональный header `Idempotency-Key: <строка ≤128 символов>` "
        "При повторном вызове с тем же ключом для того же "
        "`X-User-Id` вернётся ранее созданный `jobId` без повторного "
        "списания токенов. Используйте для безопасного retry при сетевых "
        "сбоях.\n\n"
        "### Voice (опционально)\n\n"
        "`voiceUrl` — URL голосового референса. Получается через "
        "`POST /v1/uploads/voice` (двухшаговый flow).\n\n"
        "### Поля payload\n\n"
        "* `beatId` — UUID одного из битов из `/v1/beats`.\n"
        "* `instruments` — sample-элементы для всех 10 категорий "
        "(`auxiliary` — ровно 3 элемента).\n"
        "* `equalizer.tempo`: 30..160, `*Density`: 0..10.\n"
        "* `production` — null или одно из 13 значений: studio, loFi, "
        "ethereal, aggressive, radio, live, acapella, autotuned, reverb, "
        "compressed, warm, crisp, distorted.\n"
        "* `pitch` — null или одно из 9: bass, baritone, tenor, alto, "
        "soprano, falsetto, whisper, chest, balanced.\n"
        "* `storeStems` — если `true`, готовый трек будет содержать `stems`.\n"
        "* `desiredDurationSeconds` — 5..600 секунд, используется для "
        "расчёта токенов в режиме `per_minute`."
    ),
    responses=MUSIC_ERROR_RESPONSES,
)
async def generate_track(
    body: GenerateTrackRequest,
    user: Annotated[MusicUser, Depends(get_music_user)],
    service: Annotated[GenerationService, Depends(_get_generation_service)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Опциональный ключ для безопасного retry. При повторном "
                "вызове с тем же ключом и тем же пользователем вернётся "
                "ранее созданный jobId без повторного списания токенов "
            ),
            max_length=128,
        ),
    ] = None,
) -> GenerateTrackResponse:
    payload = body.model_dump(mode="json", by_alias=False)
    payload["beat_id"] = str(body.beat_id)  # JSON-friendly
    key = (idempotency_key or "").strip() or None
    result = await service.create_job(
        user_id=user.id,
        request_payload=payload,
        store_stems=body.store_stems,
        desired_duration_seconds=body.desired_duration_seconds,
        client_idempotency_key=key,
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
    summary="Статус задания генерации (с pipeline)",
    description=(
        "Возвращает текущее состояние задания: `status` "
        "(`queued|processing|succeeded|failed|canceled`), `stage` "
        "(текущая стадия пайплайна) и `pipeline` — массив всех 8 стадий "
        "с их статусами (`pending|running|succeeded|failed|skipped`) "
        "и таймстампами.\n\n"
        "При успехе содержит `trackId` — по нему можно забрать готовый "
        "трек через `GET /v1/tracks/{trackId}`."
    ),
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
    stage_events = await service.list_stage_events(user_id=user.id, job_id=job_id)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message,
        track_id=track.id if track else None,
        created_at=job.created_at,
        finished_at=job.finished_at,
        pipeline=[StageEntry.model_validate(e) for e in stage_events],
    )


@router.get(
    "/tracks/{track_id}",
    response_model=TrackResponse,
    response_model_by_alias=True,
    summary="Готовый трек (audioUrl + опционально stems)",
    description=(
        "Возвращает финальный результат генерации:\n\n"
        "* `audioUrl` — URL аудио-файла на CDN fal.\n"
        "* `durationSeconds` — фактическая длительность.\n"
        "* `stems` — карта стемов (vocals, drums, bass, …), **только** если "
        "при запросе генерации был передан `storeStems: true`\n\n"
        "Трек становится доступным после успешного завершения пайплайна "
        "(`status: succeeded` в `/v1/tracks/jobs/{jobId}`)."
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
