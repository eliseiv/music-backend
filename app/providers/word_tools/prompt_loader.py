from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.providers.word_tools.criteria import CRITERIA_CODES

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^\s*#\s*version\s*:\s*(\S+)\s*$")


@dataclass(frozen=True)
class PromptTemplate:
    criterion: str
    body: str
    version: str
    content_sha256: str

    def render(self, *, query: str, limit: int) -> str:
        try:
            return self.body.format(query=query, limit=limit)
        except KeyError as exc:
            raise PromptRenderError(
                f"Prompt for {self.criterion!r} is missing placeholder: {exc.args[0]}"
            ) from exc


class PromptRenderError(RuntimeError):
    pass


class PromptLoader:
    def __init__(self, prompts_dir: Path) -> None:
        self._prompts_dir = Path(prompts_dir)
        self._templates: dict[str, PromptTemplate] = {}
        self._shared_system: str = ""

    def load(self) -> None:
        if not self._prompts_dir.is_dir():
            raise FileNotFoundError(
                f"Prompts directory does not exist: {self._prompts_dir}"
            )

        shared = self._prompts_dir / "_shared" / "system.txt"
        if not shared.is_file():
            raise FileNotFoundError(
                f"Shared system prompt missing: {shared}"
            )
        self._shared_system = shared.read_text(encoding="utf-8").strip()

        loaded: dict[str, PromptTemplate] = {}
        for code in CRITERIA_CODES:
            file_path = self._prompts_dir / f"{code}.txt"
            if not file_path.is_file():
                raise FileNotFoundError(
                    f"Prompt template missing for criterion {code!r}: {file_path}"
                )
            raw = file_path.read_text(encoding="utf-8")
            version, body = self._parse_version_directive(raw)
            sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if version is None:
                version = sha[:8]
            loaded[code] = PromptTemplate(
                criterion=code,
                body=body,
                version=version,
                content_sha256=sha,
            )
        self._templates = loaded
        logger.info(
            "Loaded %d prompt templates from %s", len(loaded), self._prompts_dir
        )

    def get(self, criterion: str) -> PromptTemplate:
        try:
            return self._templates[criterion]
        except KeyError as exc:
            raise KeyError(f"Unknown criterion: {criterion}") from exc

    @property
    def shared_system(self) -> str:
        return self._shared_system

    @staticmethod
    def _parse_version_directive(raw: str) -> tuple[str | None, str]:
        lines = raw.splitlines()
        version: str | None = None
        kept: list[str] = []
        directive_consumed = False
        for line in lines:
            if not directive_consumed:
                m = _VERSION_RE.match(line)
                if m:
                    version = m.group(1)
                    directive_consumed = True
                    continue
                if line.strip() == "":
                    continue
                directive_consumed = True
            kept.append(line)
        return version, "\n".join(kept).strip()
