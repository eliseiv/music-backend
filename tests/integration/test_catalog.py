from __future__ import annotations


async def test_list_beats_returns_active(app_client, auth_headers, seed_beats):
    r = await app_client.get("/v1/beats", headers=auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert "beats" in body
    assert len(body["beats"]) == 1
    beat = body["beats"][0]
    assert beat["genre"] == "electronic_dance"
    assert beat["audioUrl"].startswith("https://")


async def test_list_samples_groups_by_10_categories(
    app_client, auth_headers, seed_samples
):
    r = await app_client.get("/v1/samples", headers=auth_headers())
    assert r.status_code == 200
    body = r.json()
    cats = body["categories"]
    # Все 10 категорий присутствуют (даже если пустые)
    expected = {
        "bass", "lead", "chord",
        "kick", "snare", "closedHiHat", "openHiHat", "auxiliary",
        "mixing", "soundEffects",
    }
    # Pydantic переводит ключи через camelCase aliasing для CamelModel,
    # но dict-ключи возвращаются как есть из БД (snake_case _CATEGORY_KEY).
    # Проверяем snake_case ключи (как мы их положили).
    snake_expected = {
        "bass", "lead", "chord",
        "kick", "snare", "closed_hi_hat", "open_hi_hat", "auxiliary",
        "mixing", "sound_effects",
    }
    assert set(cats.keys()) == snake_expected


async def test_samples_tags_for_harmonic_drums_present(
    app_client, auth_headers, seed_samples
):
    r = await app_client.get("/v1/samples", headers=auth_headers())
    body = r.json()
    bass = body["categories"]["bass"]
    assert len(bass) == 1
    assert "all_instruments" in bass[0]["tags"]
    drums = body["categories"]["kick"]
    assert "all_drums" in drums[0]["tags"]


async def test_samples_mixing_and_sound_effects_no_tags(
    app_client, auth_headers, seed_samples
):
    r = await app_client.get("/v1/samples", headers=auth_headers())
    body = r.json()
    assert body["categories"]["mixing"][0]["tags"] == []
    assert body["categories"]["sound_effects"][0]["tags"] == []
