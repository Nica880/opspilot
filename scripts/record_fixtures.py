"""Record a few real, public GitHub API responses into tests/fixtures/github/.

Unauthenticated on purpose (60 req/h is plenty): fixtures must never be recorded with a write token.
Usage: uv run python scripts/record_fixtures.py
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "github"
API = "https://api.github.com"
KEEP_HEADERS = ("etag", "link", "x-ratelimit-remaining", "x-ratelimit-reset", "content-type")


def fixture_name(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", parsed.path.strip("/"))
    query = re.sub(r"[^a-zA-Z0-9]+", "_", parsed.query)
    return f"{slug}__{query}.json" if query else f"{slug}.json"


def fetch(url: str) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 - fixed https host
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "opspilot-fixtures"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() in KEEP_HEADERS}
        return {
            "url": url,
            "status": resp.status,
            "headers": headers,
            "body": json.loads(resp.read()),
        }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    urls = [
        f"{API}/repos/fastapi/fastapi",
        f"{API}/repos/pydantic/pydantic",
        f"{API}/repos/astral-sh/uv",
        f"{API}/repos/fastapi/fastapi/issues?state=all&sort=created&direction=desc&per_page=5",
        f"{API}/repos/pydantic/pydantic/issues?state=all&sort=created&direction=desc&per_page=5",
        f"{API}/repos/astral-sh/uv/issues?state=all&sort=created&direction=desc&per_page=5",
        f"{API}/repos/astral-sh/uv/issues?state=closed&labels=duplicate&per_page=5",
        f"{API}/repos/pydantic/pydantic/issues/comments?sort=updated&direction=desc&per_page=5",
    ]
    for url in urls:
        record = fetch(url)
        (OUT / fixture_name(url)).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        print(f"recorded {url}")


if __name__ == "__main__":
    main()
