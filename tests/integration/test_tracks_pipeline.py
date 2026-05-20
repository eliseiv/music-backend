from __future__ import annotations

import pytest

from tests.integration.conftest import build_generate_payload


@pytest.mark.asyncio
async def test_full_lifecycle_no_voice(
    app_client,
    auth_headers,
    fake_fal,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-pl-1", tokens=10)
    payload = build_generate_payload(seed_beats)
    payload["enableRefine"] = True  # opt-in для полного пайплайна
    h = auth_headers("u-pl-1")

    # 1. generate
    r = await app_client.post(
        "/v1/tracks/generate", headers=h, json=payload
    )
    assert r.status_code == 200, r.json()
    job_id = r.json()["jobId"]

    # Сабмит в fake_fal принят: один вызов music + reserved id
    assert len(fake_fal.calls_music) == 1
    music_request_id = f"fake-music-{len(fake_fal.calls_music)}"

    # 2. webhook: music_generation completed
    resp = await fake_fal.emit_webhook(
        app_client,
        request_id=music_request_id,
        status="completed",
        audio_url="https://cdn.test/music.mp3",
        duration_seconds=45.0,
        event_id="evt-music-1",
    )
    assert resp.status_code == 200

    # Поскольку beat_id + enable_refine → переход в audio_to_audio_refine
    assert len(fake_fal.calls_refine) == 1
    refine_request_id = f"fake-refine-{len(fake_fal.calls_refine)}"

    # 3. webhook: audio_to_audio_refine completed
    resp = await fake_fal.emit_webhook(
        app_client,
        request_id=refine_request_id,
        status="completed",
        audio_url="https://cdn.test/refined.mp3",
        duration_seconds=45.0,
        event_id="evt-refine-1",
    )
    assert resp.status_code == 200

    # Voice не передан → vocal_tts skipped, переход в finalize → succeeded
    job_status = await app_client.get(
        f"/v1/tracks/jobs/{job_id}", headers=h
    )
    assert job_status.status_code == 200
    body = job_status.json()
    assert body["status"] == "succeeded"
    assert body["trackId"] is not None
    stages = {e["stage"]: e["status"] for e in body["pipeline"]}
    assert stages["prepare_prompt"] == "succeeded"
    assert stages["music_generation"] == "succeeded"
    assert stages["audio_to_audio_refine"] == "succeeded"
    assert stages["vocal_tts"] == "skipped"
    # Без vocal_tts нечего микшировать → mix_master skipped, финал = refine
    assert stages["mix_master"] == "skipped"
    assert stages["upload_cdn"] == "succeeded"
    assert stages["finalize"] == "succeeded"

    # 4. track endpoint
    track_id = body["trackId"]
    tr = await app_client.get(f"/v1/tracks/{track_id}", headers=h)
    assert tr.status_code == 200
    track = tr.json()
    assert track["audioUrl"] == "https://cdn.test/refined.mp3"

    # 5. capture: токены списались (1 per_track)
    bal = await app_client.get("/v1/tokens/balance", headers=h)
    assert bal.json() == {"available": 9, "reserved": 0, "frozen": False}


@pytest.mark.asyncio
async def test_failed_music_triggers_fallback_then_release(
    app_client,
    auth_headers,
    fake_fal,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    """При fail от minimax-music Pipeline пробует stable-audio (fallback).
    Если и fallback fail — job окончательно failed + токены возвращены."""
    await make_user_with_subscription("u-fail", tokens=5)
    payload = build_generate_payload(seed_beats)
    h = auth_headers("u-fail")

    r = await app_client.post(
        "/v1/tracks/generate", headers=h, json=payload
    )
    job_id = r.json()["jobId"]
    music_rid = f"fake-music-{len(fake_fal.calls_music)}"

    # 1) fal сообщает failed от minimax-music → Pipeline.fail должен
    # автоматически сабмитить stable-audio (fallback)
    resp = await fake_fal.emit_webhook(
        app_client,
        request_id=music_rid,
        status="failed",
        error="model exploded",
        event_id="evt-music-fail",
    )
    assert resp.status_code == 200

    # Проверка: fallback вызван (был submit к stable-audio)
    assert hasattr(fake_fal, "calls_stable") and len(fake_fal.calls_stable) == 1

    # Job всё ещё processing (ждёт результат stable-audio)
    job_status = await app_client.get(
        f"/v1/tracks/jobs/{job_id}", headers=h
    )
    assert job_status.json()["status"] == "processing"

    # 2) stable-audio тоже fail → теперь окончательный fail + release
    stable_rid = f"fake-stable-{len(fake_fal.calls_stable)}"
    resp = await fake_fal.emit_webhook(
        app_client,
        request_id=stable_rid,
        status="failed",
        error="stable-audio exploded too",
        event_id="evt-stable-fail",
    )
    assert resp.status_code == 200

    job_status = await app_client.get(
        f"/v1/tracks/jobs/{job_id}", headers=h
    )
    assert job_status.json()["status"] == "failed"

    # Токены возвращены
    bal = await app_client.get("/v1/tokens/balance", headers=h)
    assert bal.json() == {"available": 5, "reserved": 0, "frozen": False}


@pytest.mark.asyncio
async def test_full_lifecycle_with_voice_clone(
    app_client,
    auth_headers,
    fake_fal,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    """С voice_url + lyrics_prompt:
    1. LLM генерирует lyrics
    2. music_generation → music
    3. voice_clone → custom_voice_id
    4. vocal_tts (speech) на клоне голоса → vocal
    5. mix_master ffmpeg должен попытаться смикшировать music + vocal
       (в тестах без ffmpeg → skipped, vocal в stems.vocal)
    """
    await make_user_with_subscription("u-voice-1", tokens=10)
    payload = build_generate_payload(seed_beats)
    payload["voiceUrl"] = "https://cdn.test/user-voice.wav"
    payload["lyricsPrompt"] = "summer night dreams"
    h = auth_headers("u-voice-1")

    r = await app_client.post("/v1/tracks/generate", headers=h, json=payload)
    assert r.status_code == 200, r.json()
    job_id = r.json()["jobId"]

    # LLM должна была быть вызвана с темой
    assert hasattr(fake_fal, "calls_lyrics") and len(fake_fal.calls_lyrics) == 1
    assert fake_fal.calls_lyrics[0]["prompt"] == "summer night dreams"

    # music_generation submit (без voice_url в reference, только beat)
    assert len(fake_fal.calls_music) == 1
    assert fake_fal.calls_music[0]["lyrics"].startswith("Fake lyrics about")
    # voice_url НЕ должен попасть в reference_audio_url music
    assert "user-voice.wav" not in (
        fake_fal.calls_music[0].get("reference_audio_url") or ""
    )

    # 1) music webhook
    music_rid = "fake-music-1"
    await fake_fal.emit_webhook(
        app_client, request_id=music_rid, status="completed",
        audio_url="https://cdn.test/music.mp3", duration_seconds=30.0,
        event_id="evt-vm-1",
    )

    # 2) После music — voice_clone (inline) и затем submit_speech
    assert hasattr(fake_fal, "calls_voice_clone")
    assert fake_fal.calls_voice_clone[0]["audio_url"] == "https://cdn.test/user-voice.wav"
    assert len(fake_fal.calls_speech) == 1
    # text speech — это сгенерированные LLM lyrics, не lyrics_prompt
    assert fake_fal.calls_speech[0]["text"].startswith("Fake lyrics about")
    assert fake_fal.calls_speech[0]["voice_id"] == "fake-cloned-voice-1"

    # 3) vocal_tts webhook
    speech_rid = "fake-speech-1"
    await fake_fal.emit_webhook(
        app_client, request_id=speech_rid, status="completed",
        audio_url="https://cdn.test/vocal.mp3", duration_seconds=15.0,
        event_id="evt-vt-1",
    )

    job_status = await app_client.get(f"/v1/tracks/jobs/{job_id}", headers=h)
    body = job_status.json()
    assert body["status"] == "succeeded", body
    stages = {e["stage"]: e["status"] for e in body["pipeline"]}
    assert stages["lyrics"] == "succeeded"
    assert stages["music_generation"] == "succeeded"
    assert stages["vocal_tts"] == "succeeded"
    # mix_master: либо succeeded (если ffmpeg в системе), либо skipped
    assert stages["mix_master"] in ("succeeded", "skipped")
    assert stages["finalize"] == "succeeded"


@pytest.mark.asyncio
async def test_stems_only_when_store_stems_true(
    app_client,
    auth_headers,
    fake_fal,
    seed_beats,
    seed_pricing,
    make_user_with_subscription,
):
    await make_user_with_subscription("u-stems", tokens=5)
    payload = build_generate_payload(seed_beats)
    payload["storeStems"] = True
    payload["enableRefine"] = True
    h = auth_headers("u-stems")

    r = await app_client.post(
        "/v1/tracks/generate", headers=h, json=payload
    )
    job_id = r.json()["jobId"]
    music_rid = f"fake-music-{len(fake_fal.calls_music)}"
    await fake_fal.emit_webhook(
        app_client,
        request_id=music_rid,
        status="completed",
        audio_url="https://cdn.test/music.mp3",
        duration_seconds=30.0,
        stems={"vocals": "https://cdn.test/v.mp3", "drums": "https://cdn.test/d.mp3"},
        event_id="evt-stems-music",
    )
    refine_rid = f"fake-refine-{len(fake_fal.calls_refine)}"
    await fake_fal.emit_webhook(
        app_client,
        request_id=refine_rid,
        status="completed",
        audio_url="https://cdn.test/refined.mp3",
        duration_seconds=30.0,
        stems={"vocals": "https://cdn.test/v2.mp3"},
        event_id="evt-stems-refine",
    )
    job_status = await app_client.get(
        f"/v1/tracks/jobs/{job_id}", headers=h
    )
    track_id = job_status.json()["trackId"]
    tr = await app_client.get(f"/v1/tracks/{track_id}", headers=h)
    assert tr.json()["stems"] is not None
