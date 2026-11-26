"""Per-repo areas and label vocabulary.

Labels are the ones maintainers actually use: `top_labels()` derives them from the corpus
(labels applied at least `min_count` times), so proposals can be scored against history.
The defaults below are used when the corpus is empty (CI, first run).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

AREAS: dict[str, list[str]] = {
    "fastapi/fastapi": [
        "routing",
        "dependencies",
        "validation",
        "openapi",
        "security",
        "websockets",
        "middleware",
        "background-tasks",
        "testing",
        "deployment",
        "docs",
        "other",
    ],
    "pydantic/pydantic": [
        "validation",
        "serialization",
        "json-schema",
        "dataclasses",
        "types",
        "typing",
        "settings",
        "performance",
        "migration-v1-v2",
        "docs",
        "other",
    ],
    "astral-sh/uv": [
        "resolver",
        "installer",
        "lockfile",
        "workspaces",
        "python-management",
        "build",
        "cli",
        "configuration",
        "network",
        "cache",
        "tools",
        "docs",
        "other",
    ],
}
GENERIC_AREAS = ["core", "docs", "other"]

DEFAULT_LABELS: dict[str, list[str]] = {
    "fastapi/fastapi": ["bug", "feature", "question", "docs", "answered", "investigate"],
    "pydantic/pydantic": [
        "bug V2",
        "feature request",
        "question",
        "documentation",
        "pending",
        "unconfirmed",
    ],
    "astral-sh/uv": [
        "bug",
        "enhancement",
        "question",
        "documentation",
        "needs-mre",
        "resolver",
        "windows",
        "cli",
        "error messages",
        "configuration",
        "workspaces",
        "python",
        "build-backend",
    ],
}
# Labels that are workflow noise, not triage signal.
IGNORED_LABELS = frozenset(
    {"internal", "refactor", "upgrade", "relnotes-ignore", "dependencies", "bot:ai-policy-close"}
)


def areas_for(repo: str) -> list[str]:
    return AREAS.get(repo, GENERIC_AREAS)


def top_labels(
    label_lists: Iterable[list[str]], *, min_count: int = 5, limit: int = 25
) -> list[str]:
    counts = Counter(
        label for labels in label_lists for label in labels if label not in IGNORED_LABELS
    )
    return [label for label, n in counts.most_common(limit) if n >= min_count]


def labels_for(repo: str, observed: list[str] | None = None) -> list[str]:
    return (
        observed if observed else DEFAULT_LABELS.get(repo, ["bug", "feature", "question", "docs"])
    )
