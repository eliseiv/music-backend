from __future__ import annotations

from uuid import UUID

from pydantic import ConfigDict, Field

from app.music.enums import BeatGenre
from app.schemas.common import CamelModel


class BeatItem(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "f2bf8c34-4125-4c98-a838-40c22fabb148",
                    "genre": "electronic_dance",
                    "tags": ["house", "edm"],
                    "title": "Pulse 124",
                    "audioUrl": "https://cdn.example/beats/pulse_124.mp3",
                    "previewUrl": None,
                    "durationSeconds": 32,
                    "bpm": 124,
                    "key": "Am",
                }
            ]
        },
    )

    id: UUID
    genre: BeatGenre
    tags: list[str] = Field(
        default_factory=list,
        description=(
            "Поджанры (house, edm, trap, lofi_hip_hop и т.п.). "
            "Допустимые значения зависят от genre — см. BEAT_SUBGENRE_TAGS."
        ),
    )
    title: str
    audio_url: str
    preview_url: str | None = None
    duration_seconds: int | None = None
    bpm: int | None = None
    key: str | None = None


class BeatsResponse(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    beats: list[BeatItem] = Field(description="Список активных битов.")
