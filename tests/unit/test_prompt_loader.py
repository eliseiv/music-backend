from __future__ import annotations

from pathlib import Path

import pytest

from app.providers.word_tools.criteria import CRITERIA_CODES
from app.providers.word_tools.prompt_loader import PromptLoader


def _seed_prompts(root: Path, *, missing: set[str] | None = None) -> None:
    missing = missing or set()
    shared = root / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "system.txt").write_text("system prompt", encoding="utf-8")
    for code in CRITERIA_CODES:
        if code in missing:
            continue
        body = f"# version: {code}.test\nReturn things for {{query}} (limit {{limit}}).\n"
        (root / f"{code}.txt").write_text(body, encoding="utf-8")


def test_load_all_templates(tmp_path: Path):
    _seed_prompts(tmp_path)
    loader = PromptLoader(tmp_path)
    loader.load()
    assert loader.shared_system == "system prompt"
    for code in CRITERIA_CODES:
        tmpl = loader.get(code)
        assert tmpl.version == f"{code}.test"
        rendered = tmpl.render(query="x", limit=5)
        assert "x" in rendered and "5" in rendered


def test_load_missing_criterion_fails(tmp_path: Path):
    _seed_prompts(tmp_path, missing={"rhymes"})
    loader = PromptLoader(tmp_path)
    with pytest.raises(FileNotFoundError, match="rhymes"):
        loader.load()


def test_version_falls_back_to_sha(tmp_path: Path):
    _seed_prompts(tmp_path)
    target = tmp_path / "rhymes.txt"
    target.write_text("Plain prompt for {query} {limit}\n", encoding="utf-8")
    loader = PromptLoader(tmp_path)
    loader.load()
    tmpl = loader.get("rhymes")
    assert len(tmpl.version) == 8
    assert tmpl.version != "rhymes.test"


def test_real_prompts_dir_loads(tmp_path: Path):
    project_prompts = Path(__file__).resolve().parents[2] / "prompts"
    if not project_prompts.exists():
        pytest.skip("project prompts dir not present")
    loader = PromptLoader(project_prompts)
    loader.load()
    assert {c for c in CRITERIA_CODES} <= {
        p.stem for p in project_prompts.glob("*.txt")
    }
