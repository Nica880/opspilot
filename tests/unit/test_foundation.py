from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import create_app
from common.config import Settings
from common.migrate import pending


def test_repos_env_is_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOS", "a/b, c/d")
    assert Settings(_env_file=None).repos == ("a/b", "c/d")


@pytest.mark.parametrize("bad", ["noslash", "a/b/c", "/x", "x/"])
def test_sandbox_repo_must_be_owner_slash_name(bad: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sandbox_repo=bad)


def test_migrations_are_ordered_and_skip_applied() -> None:
    names = [p.stem for p in pending(set())]
    assert names == sorted(names)
    assert "001_init" in names
    assert "001_init" not in [p.stem for p in pending({"001_init"})]


def test_healthz() -> None:
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
