from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def load_github_fixture(name: str) -> dict[str, Any]:
    """A recorded response: {"url", "status", "headers", "body"}."""
    data: dict[str, Any] = json.loads((FIXTURES / "github" / name).read_text(encoding="utf-8"))
    return data
