from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.music import beats as music_beats
from app.api.v1.music import samples as music_samples
from app.api.v1.music import tokens as music_tokens
from app.api.v1.music import tracks as music_tracks
from app.api.v1.music import uploads as music_uploads
from app.api.v1.music import webhooks as music_webhooks

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(music_beats.router)
api_v1_router.include_router(music_samples.router)
api_v1_router.include_router(music_tokens.router)
api_v1_router.include_router(music_tracks.router)
api_v1_router.include_router(music_uploads.router)
api_v1_router.include_router(music_webhooks.router)
