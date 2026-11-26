"""Versioned prompts. Released versions are immutable: copy a folder to change a prompt.

A version only needs the files it changes; missing files fall back to v1.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent
VERSIONS = ("v1", "v2")


def load_prompt(version: str, name: str, **values: str) -> str:
    if version not in VERSIONS:
        raise ValueError(f"unknown prompt version {version!r}")
    path = PROMPTS_DIR / version / f"{name}.md"
    if not path.exists():
        path = PROMPTS_DIR / "v1" / f"{name}.md"
    return Template(path.read_text(encoding="utf-8")).substitute(values)
