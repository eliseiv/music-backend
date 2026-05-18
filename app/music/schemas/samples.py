from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from pydantic import ConfigDict, Field

from app.music.enums import SampleCategory
from app.schemas.common import CamelModel


class SampleItem(CamelModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "7457f283-b166-4d29-830c-dae127dd799d",
                    "url": "https://cdn.example/samples/upright_walk.wav",
                    "tags": ["all_instruments", "jazz_trio"],
                }
            ]
        },
    )

    id: UUID
    url: str = Field(
        description="URL аудио-файла сэмпла.",
        examples=["https://cdn.example/samples/upright_walk.wav"],
    )
    tags: list[str] = Field(default_factory=list)


class SamplesByCategoryResponse(CamelModel):
    """Сэмплы, сгруппированные по 10 категориям в формате.

    Поля категорий из называются `kick`, `snare`, `closed_hi_hat`,
    `open_hi_hat`, `auxiliary`, `bass`, `lead`, `chord`, `mixing`,
    `sound_effects` — в ответ отдаём именно их (без префиксов
    `harmonic_`/`drums_`), чтобы UI получил структуру as-is.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "categories": {
                        "bass": [
                            {
                                "id": "uuid",
                                "url": "https://cdn.example/samples/sub_saw.wav",
                                "tags": ["all_instruments", "synth_haven"],
                            }
                        ],
                        "lead": [],
                        "chord": [],
                        "kick": [],
                        "snare": [],
                        "closed_hi_hat": [],
                        "open_hi_hat": [],
                        "auxiliary": [],
                        "mixing": [
                            {
                                "id": "uuid",
                                "url": "https://cdn.example/samples/master.wav",
                                "tags": [],
                            }
                        ],
                        "sound_effects": [],
                    }
                }
            ]
        },
    )

    categories: dict[str, list[SampleItem]] = Field(
        description="Map категории → список сэмплов.",
    )


_CATEGORY_KEY = {
    SampleCategory.harmonic_bass: "bass",
    SampleCategory.harmonic_lead: "lead",
    SampleCategory.harmonic_chord: "chord",
    SampleCategory.drums_kick: "kick",
    SampleCategory.drums_snare: "snare",
    SampleCategory.drums_closed_hihat: "closed_hi_hat",
    SampleCategory.drums_open_hihat: "open_hi_hat",
    SampleCategory.drums_auxiliary: "auxiliary",
    SampleCategory.mixing: "mixing",
    SampleCategory.sound_effects: "sound_effects",
}


def category_response_key(category: SampleCategory) -> str:
    return _CATEGORY_KEY[category]


def group_samples_by_category(samples) -> dict[str, list[SampleItem]]:
    grouped: dict[str, list[SampleItem]] = defaultdict(list)
    for s in samples:
        key = category_response_key(s.category)
        grouped[key].append(SampleItem(id=s.id, url=s.audio_url, tags=list(s.tags)))
    ordered: dict[str, list[SampleItem]] = {}
    for cat in (
        SampleCategory.harmonic_bass,
        SampleCategory.harmonic_lead,
        SampleCategory.harmonic_chord,
        SampleCategory.drums_kick,
        SampleCategory.drums_snare,
        SampleCategory.drums_closed_hihat,
        SampleCategory.drums_open_hihat,
        SampleCategory.drums_auxiliary,
        SampleCategory.mixing,
        SampleCategory.sound_effects,
    ):
        key = category_response_key(cat)
        ordered[key] = grouped.get(key, [])
    return ordered
