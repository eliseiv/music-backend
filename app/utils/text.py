from __future__ import annotations

import unicodedata


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def normalize_message(text: str) -> str:
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    return normalized.strip()
