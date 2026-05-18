from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator

from app.music.enums import JobStage, JobStatus
from app.music.tags import PITCH_VALUES, PRODUCTION_VALUES
from app.schemas.common import CamelModel


# --- input schemas ---


class _SampleRef(CamelModel):
    model_config = ConfigDict(extra="forbid")
    sample_url: str = Field(min_length=1, max_length=2048)

    @field_validator("sample_url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("sample_url must be http(s)")
        return v


class _HarmonicGroup(CamelModel):
    model_config = ConfigDict(extra="forbid")
    bass: _SampleRef
    lead: _SampleRef
    chord: _SampleRef


class _DrumsGroup(CamelModel):
    model_config = ConfigDict(extra="forbid")
    kick: _SampleRef
    snare: _SampleRef
    open_hihat: _SampleRef
    closed_hihat: _SampleRef
    auxiliary: list[_SampleRef] = Field(min_length=3, max_length=3)


class _Instruments(CamelModel):
    model_config = ConfigDict(extra="forbid")
    harmonic: _HarmonicGroup
    drums: _DrumsGroup
    mixing: _SampleRef
    sound_effects: _SampleRef


class _Equalizer(CamelModel):
    model_config = ConfigDict(extra="forbid")
    tempo: int = Field(ge=30, le=160)
    lead_density: int = Field(ge=0, le=10)
    bass_density: int = Field(ge=0, le=10)
    chord_density: int = Field(ge=0, le=10)
    drum_density: int = Field(ge=0, le=10)


class GenerateTrackRequest(CamelModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "beatId": "f55ad4b1-0819-4062-9289-3ad56cea9671",
                    "instruments": {
                        "harmonic": {
                            "bass": {"sampleUrl": "https://placeholder.example/samples/harmonic_bass/sub_saw.wav"},
                            "lead": {"sampleUrl": "https://placeholder.example/samples/harmonic_lead/rhodes_sparkle.wav"},
                            "chord": {"sampleUrl": "https://placeholder.example/samples/harmonic_chord/acoustic_strum.wav"},
                        },
                        "drums": {
                            "kick": {"sampleUrl": "https://placeholder.example/samples/drums_kick/808_boom.wav"},
                            "snare": {"sampleUrl": "https://placeholder.example/samples/drums_snare/studio_snare.wav"},
                            "openHihat": {"sampleUrl": "https://placeholder.example/samples/drums_open_hihat/open_sizzle.wav"},
                            "closedHihat": {"sampleUrl": "https://placeholder.example/samples/drums_closed_hihat/tight_hat.wav"},
                            "auxiliary": [
                                {"sampleUrl": "https://placeholder.example/samples/drums_auxiliary/glitch_fx.wav"},
                                {"sampleUrl": "https://placeholder.example/samples/drums_auxiliary/glitch_fx.wav"},
                                {"sampleUrl": "https://placeholder.example/samples/drums_auxiliary/glitch_fx.wav"},
                            ],
                        },
                        "mixing": {"sampleUrl": "https://placeholder.example/samples/mixing/master_saturator.wav"},
                        "soundEffects": {"sampleUrl": "https://placeholder.example/samples/sound_effects/riser.wav"},
                    },
                    "equalizer": {
                        "tempo": 124,
                        "leadDensity": 7,
                        "bassDensity": 8,
                        "chordDensity": 5,
                        "drumDensity": 9,
                    },
                    "lyricsPrompt": "Sunset lullaby",
                    "voiceUrl": None,
                    "production": "studio",
                    "pitch": "balanced",
                    "storeStems": False,
                    "language": "en",
                    "desiredDurationSeconds": 60,
                }
            ]
        },
    )

    beat_id: UUID
    instruments: _Instruments
    equalizer: _Equalizer
    lyrics_prompt: str | None = Field(default=None, max_length=2000)
    voice_url: str | None = Field(default=None, max_length=2048)
    production: str | None = None
    pitch: str | None = None
    store_stems: bool = False
    language: str = Field(default="en", min_length=2, max_length=8)
    desired_duration_seconds: int | None = Field(default=None, ge=5, le=600)

    @field_validator("voice_url")
    @classmethod
    def _voice_http(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.startswith(("http://", "https://")):
            raise ValueError("voice_url must be http(s)")
        return v

    @field_validator("production")
    @classmethod
    def _production_value(cls, v: str | None) -> str | None:
        if v is None or v in PRODUCTION_VALUES:
            return v
        raise ValueError(
            f"production must be one of {sorted(PRODUCTION_VALUES)} or null"
        )

    @field_validator("pitch")
    @classmethod
    def _pitch_value(cls, v: str | None) -> str | None:
        if v is None or v in PITCH_VALUES:
            return v
        raise ValueError(
            f"pitch must be one of {sorted(PITCH_VALUES)} or null"
        )


# --- response schemas ---


class GenerateTrackResponse(CamelModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "jobId": "9b8a90d1-2dab-4d9b-a48c-3061a6f8a8e1",
                    "status": "queued",
                    "tokensReserved": 1,
                }
            ]
        }
    )

    job_id: UUID
    status: JobStatus
    tokens_reserved: int


class StageEntry(CamelModel):
    """Запись о статусе одной стадии пайплайна (ТЗ §9.1.2 — прогресс и шаги)."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    stage: JobStage
    status: str = Field(
        description="pending | running | succeeded | failed | skipped",
        examples=["succeeded"],
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class JobStatusResponse(CamelModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    job_id: UUID
    status: JobStatus
    stage: JobStage | None = None
    error_code: str | None = None
    error_message: str | None = None
    track_id: UUID | None = None
    created_at: datetime
    finished_at: datetime | None = None
    pipeline: list[StageEntry] = Field(
        default_factory=list,
        description="Прогресс и шаги пайплайна (ТЗ §9.1.2).",
    )


class TrackResponse(CamelModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    job_id: UUID
    audio_url: str
    duration_seconds: float
    stems: dict | None = None
    created_at: datetime
