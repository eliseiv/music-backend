from __future__ import annotations

from app.providers.word_tools.criteria import CRITERIA


async def test_criteria_returns_all_16_in_order(app_client):
    r = await app_client.get("/api/v1/word-tools/criteria")
    assert r.status_code == 200
    body = r.json()
    codes = [c["code"] for c in body["criteria"]]
    assert codes == [code for code, _ in CRITERIA]
    assert len(codes) == 16
