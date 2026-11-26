"""Runtime settings, read from the environment (and `.env` in local dev)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_REPOS = ("fastapi/fastapi", "pydantic/pydantic", "astral-sh/uv")


class Settings(BaseSettings):
    # Compose passes `GITHUB_TOKEN=` from .env.example as "": treat empty values as unset.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql://opspilot:opspilot@localhost:5432/opspilot"
    qdrant_url: str = "http://localhost:6333"
    kafka_bootstrap: str = "localhost:19092"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "opspilot-triage"
    ollama_url: str = "http://localhost:11434"
    phoenix_otlp_endpoint: str = "http://localhost:6006/v1/traces"

    github_token: SecretStr | None = None
    sandbox_repo: str | None = None

    repos: Annotated[tuple[str, ...], NoDecode] = DEFAULT_REPOS
    mode: Literal["small", "full"] = "small"
    small_mode_issues_per_repo: int = Field(default=700, ge=1)

    llm_provider: Literal["ollama", "fake"] = "ollama"
    llm_model: str = "qwen2.5:7b-instruct"
    judge_model: str = "llama3.1:8b"
    embed_model: str = "nomic-embed-text"
    prompt_version: str = "v1"

    @field_validator("sandbox_repo")
    @classmethod
    def _sandbox_is_owner_slash_name(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        owner, sep, name = value.partition("/")
        if not sep or not owner or not name or "/" in name:
            raise ValueError("SANDBOX_REPO must look like 'owner/name'")
        return value

    @field_validator("repos", mode="before")
    @classmethod
    def _split_repos(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
