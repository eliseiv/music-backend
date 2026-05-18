from __future__ import annotations

from typing import Final, Literal


CRITERIA: Final[list[tuple[str, str]]] = [
    ("rhymes", "Rhymes"),
    ("rhymes_advanced", "Rhymes (advanced)"),
    ("near_rhymes", "Near rhymes"),
    ("synonyms", "Synonyms"),
    ("descriptive_words", "Descriptive words"),
    ("phrases", "Phrases"),
    ("antonyms", "Antonyms"),
    ("definitions", "Definitions"),
    ("related_words", "Related words"),
    ("similar_sounding", "Similar sounding words"),
    ("similarly_spelled", "Similarly spelled words"),
    ("homophones", "Homophones"),
    ("phrase_rhymes", "Phrase rhymes"),
    ("match_consonants", "Match consonants"),
    ("match_letters", "Match these letters"),
    ("unscramble", "Unscramble (anagrams)"),
]

CRITERIA_CODES: Final[frozenset[str]] = frozenset(code for code, _ in CRITERIA)

CriterionCode = Literal[
    "rhymes",
    "rhymes_advanced",
    "near_rhymes",
    "synonyms",
    "descriptive_words",
    "phrases",
    "antonyms",
    "definitions",
    "related_words",
    "similar_sounding",
    "similarly_spelled",
    "homophones",
    "phrase_rhymes",
    "match_consonants",
    "match_letters",
    "unscramble",
]
