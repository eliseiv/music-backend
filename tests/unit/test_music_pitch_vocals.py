"""pitch → вокальные параметры: сдвиг высоты для TTS + хинт для промптов.

Регрессия на баг «в pitch шлём whisper/bass/baritone — всегда обычный вокал»:
раньше pitch попадал только строкой `Pitch: whisper` в prompt, которую
minimax-music игнорирует, а в voice_setting не уходил вовсе.
"""
from __future__ import annotations

import pytest

from app.music.services.pipeline import Pipeline, _compose_prompt
from app.music.tags import (
    PITCH_SEMITONES,
    PITCH_VALUES,
    PITCH_VOCAL_HINT,
    pitch_semitones,
    pitch_vocal_hint,
)


def test_mappings_cover_every_pitch_value():
    assert set(PITCH_SEMITONES) == PITCH_VALUES
    assert set(PITCH_VOCAL_HINT) == PITCH_VALUES


@pytest.mark.parametrize("value", sorted(PITCH_VALUES))
def test_semitones_within_minimax_range(value):
    assert -12 <= PITCH_SEMITONES[value] <= 12


def test_registers_shift_in_expected_direction():
    # Низкие регистры вниз, высокие вверх — иначе клон звучит наоборот.
    assert pitch_semitones("bass") < pitch_semitones("baritone") < 0
    assert 0 < pitch_semitones("alto") < pitch_semitones("soprano")


def test_manner_tags_do_not_shift_pitch():
    # whisper — манера подачи, а не регистр: высоту не трогаем.
    assert pitch_semitones("whisper") is None
    assert pitch_semitones("balanced") is None
    assert pitch_semitones(None) is None


def test_vocal_hint_is_prose_not_raw_tag():
    assert pitch_vocal_hint("whisper") == "whispered breathy intimate vocals"
    assert pitch_vocal_hint("bass") == "deep bass male vocals"
    assert pitch_vocal_hint(None) is None


def test_compose_prompt_uses_vocal_hint():
    prompt = _compose_prompt({"pitch": "whisper"}, None)
    assert "whispered breathy intimate vocals" in prompt
    # Старый key/value формат ушёл — именно он и игнорировался моделью.
    assert "Pitch: whisper" not in prompt


def test_compose_prompt_without_pitch_has_no_vocal_clause():
    assert "Vocals:" not in _compose_prompt({"production": "warm"}, None)


def test_ace_step_tags_carry_vocal_hint():
    tags = Pipeline._ace_step_tags({"pitch": "baritone"})
    assert "warm baritone male vocals" in tags
    # Общий хинт "vocal" не дублируем — свой уже есть.
    assert ", vocal" not in tags


def test_ace_step_tags_keep_generic_vocal_hint_without_pitch():
    assert "vocal" in Pipeline._ace_step_tags({"production": "warm"})


def test_fallback_prompt_carries_vocal_hint():
    prompt = Pipeline._fallback_prompt({"pitch": "soprano"})
    assert "soaring soprano female vocals" in prompt
