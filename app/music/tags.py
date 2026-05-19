from __future__ import annotations

from typing import Final

from app.music.enums import BeatGenre, SampleCategory


HARMONIC_TAGS: Final[frozenset[str]] = frozenset(
    {
        "all_instruments",
        "acoustic_guitars",
        "global_ensemble",
        "acoustic_instruments",
        "chill_keys",
        "seventies_fusion",
        "jazz_trio",
        "rock_n_roll",
        "soft_rock",
        "classical_strings",
        "synth_haven",
        "smooth_pop",
        "carolina_trap_set",
        "brass_and_winds",
    }
)

DRUM_TAGS: Final[frozenset[str]] = frozenset(
    {
        "all_drums",
        "acoustic",
        "dusty",
        "edm",
        "experimental",
        "trap_808",
        "vintage_electronic",
    }
)

# tags.
HARMONIC_CATEGORIES: Final[frozenset[SampleCategory]] = frozenset(
    {
        SampleCategory.harmonic_bass,
        SampleCategory.harmonic_lead,
        SampleCategory.harmonic_chord,
    }
)

DRUMS_CATEGORIES: Final[frozenset[SampleCategory]] = frozenset(
    {
        SampleCategory.drums_kick,
        SampleCategory.drums_snare,
        SampleCategory.drums_closed_hihat,
        SampleCategory.drums_open_hihat,
        SampleCategory.drums_auxiliary,
    }
)

UNTAGGED_CATEGORIES: Final[frozenset[SampleCategory]] = frozenset(
    {SampleCategory.mixing, SampleCategory.sound_effects}
)


def allowed_tags_for_category(category: SampleCategory) -> frozenset[str]:
    if category in HARMONIC_CATEGORIES:
        return HARMONIC_TAGS
    if category in DRUMS_CATEGORIES:
        return DRUM_TAGS
    return frozenset()


def validate_tags(category: SampleCategory, tags: list[str]) -> list[str]:
    """Return normalized tag list; raise ValueError on unknown tags."""
    allowed = allowed_tags_for_category(category)
    if category in UNTAGGED_CATEGORIES:
        if tags:
            raise ValueError(
                f"Category {category.value!r} does not accept tags"
            )
        return []
    normalized = []
    for tag in tags:
        normalized_tag = tag.strip().lower()
        if not normalized_tag:
            continue
        if normalized_tag not in allowed:
            raise ValueError(
                f"Tag {normalized_tag!r} is not allowed for category "
                f"{category.value!r}"
            )
        normalized.append(normalized_tag)
    return normalized


PRODUCTION_VALUES: Final[frozenset[str]] = frozenset(
    {
        "studio",
        "loFi",
        "ethereal",
        "aggressive",
        "radio",
        "live",
        "acapella",
        "autotuned",
        "reverb",
        "compressed",
        "warm",
        "crisp",
        "distorted",
    }
)

PITCH_VALUES: Final[frozenset[str]] = frozenset(
    {
        "bass",
        "baritone",
        "tenor",
        "alto",
        "soprano",
        "falsetto",
        "whisper",
        "chest",
        "balanced",
    }
)


# --- Beat sub-genre tags (для iOS-фильтра) ---
# Каждый бит может иметь несколько тегов, представляющих поджанр/настроение
# внутри основного жанра. Таксономия валидируется в seed-importer'е.
BEAT_SUBGENRE_TAGS: Final[dict[BeatGenre, frozenset[str]]] = {
    BeatGenre.electronic_dance: frozenset(
        {
            "house",
            "techno",
            "edm",
            "trance",
            "dubstep",
            "drum_and_bass",
            "future_bass",
            "electro",
        }
    ),
    BeatGenre.rap: frozenset(
        {
            "trap",
            "boom_bap",
            "cloud_rap",
            "drill",
            "lo_fi_rap",
            "old_school",
            "phonk",
        }
    ),
    BeatGenre.lofi: frozenset(
        {
            "lofi_hip_hop",
            "chillhop",
            "jazz_lofi",
            "vinyl",
            "study_beats",
            "rainy",
        }
    ),
    BeatGenre.global_groove: frozenset(
        {
            "afrobeat",
            "latin",
            "bossa_nova",
            "reggaeton",
            "world",
            "samba",
            "amapiano",
        }
    ),
    BeatGenre.relaxing_meditation: frozenset(
        {
            "ambient",
            "nature",
            "binaural",
            "drone",
            "spa",
            "yoga",
            "sleep",
        }
    ),
}


def allowed_beat_tags(genre: BeatGenre) -> frozenset[str]:
    return BEAT_SUBGENRE_TAGS.get(genre, frozenset())


def validate_beat_tags(genre: BeatGenre, tags: list[str]) -> list[str]:
    """Normalize + validate beat tags. Raises ValueError on unknown tag."""
    allowed = allowed_beat_tags(genre)
    normalized: list[str] = []
    for t in tags:
        norm = t.strip().lower()
        if not norm:
            continue
        if norm not in allowed:
            raise ValueError(
                f"Beat tag {norm!r} is not allowed for genre {genre.value!r}. "
                f"Allowed: {sorted(allowed)}"
            )
        normalized.append(norm)
    return normalized
