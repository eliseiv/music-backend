"""POST /v1/uploads/voice — multipart upload в fal storage."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_upload_voice_returns_url(app_client, auth_headers, fake_fal):
    fake_fal.uploaded_url = "https://fal-cdn.test/voice123.wav"
    r = await app_client.post(
        "/v1/uploads/voice",
        headers=auth_headers(),
        files={"file": ("voice.wav", b"fake-wav-bytes", "audio/wav")},
    )
    assert r.status_code == 200, r.json()
    assert r.json() == {"voiceUrl": "https://fal-cdn.test/voice123.wav"}
    assert len(fake_fal.calls_upload) == 1
    assert fake_fal.calls_upload[0]["content_type"] == "audio/wav"


@pytest.mark.asyncio
async def test_upload_voice_rejects_wrong_content_type(
    app_client, auth_headers
):
    r = await app_client.post(
        "/v1/uploads/voice",
        headers=auth_headers(),
        files={"file": ("voice.txt", b"text", "text/plain")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_voice_too_large_returns_413(
    app_client, auth_headers, settings
):
    big = b"\x00" * (settings.MUSIC_VOICE_MAX_BYTES + 1)
    r = await app_client.post(
        "/v1/uploads/voice",
        headers=auth_headers(),
        files={"file": ("big.wav", big, "audio/wav")},
    )
    assert r.status_code == 413
