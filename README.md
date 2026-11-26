# OpsPilot

OpsPilot is an agentic triage and resolution copilot for open-source issue trackers. It ingests
public GitHub issues, pull requests and docs from large OSS projects (fastapi/fastapi,
pydantic/pydantic, astral-sh/uv). For every new issue it classifies type, area and severity,
finds likely duplicates, drafts a grounded first response with citations (or refuses when there is
no evidence), proposes labels and a reproduction checklist, and sends everything to a human
approval queue. Approved actions are applied only to a sandbox repo, never upstream.

Everything runs locally and free: Ollama models, Postgres, Qdrant, Redpanda, Temporal, Phoenix.

> Work in progress. See [docs/PLAN.md](docs/PLAN.md) for milestone status.

## Quick start (macOS, Apple Silicon)

```bash
cp .env.example .env        # add GITHUB_TOKEN (read-only) and SANDBOX_REPO
make up                     # docker compose stack + migrations
```

See [docs/architecture.md](docs/architecture.md) and the ADRs in [docs/adr](docs/adr).

## License

MIT. Ingested issue data belongs to its authors and stays under each source repo's license; the
license of every ingested repo is recorded in the `repos` table.
