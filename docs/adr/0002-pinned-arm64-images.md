# ADR-002: Pinned, arm64-verified container images

- Status: accepted
- Date: 2026-11-26

## Context

Floating tags (`latest`, `16`) silently change under you, and some popular images have been
withdrawn or stopped publishing arm64 builds (official MinIO images in 2025, for example). The demo
must run on an Apple Silicon Mac.

## Decision

- Every image in `docker-compose.yml`, the Dockerfiles and the Helm chart uses an exact tag.
- `make check-images` runs `scripts/check_images.py`, which reads the registry manifest list
  (the same data `docker manifest inspect` shows) and fails unless `linux/arm64` is present.
  It needs no Docker daemon, so it also works in CI.
- Our own Python image (`opspilot/py`) is built once by the `api` service. `worker` reuses the tag
  with `pull_policy: never` and has no `build:` section, so compose never builds the same tag twice
  in parallel. `make build` builds `api` and then `web`, one after the other.

Verified on 2026-11-26:

| Image | Platforms include linux/arm64 |
|---|---|
| postgres:16.16-alpine | yes |
| qdrant/qdrant:v1.20.2 | yes |
| redpandadata/redpanda:v26.2.7 | yes |
| redpandadata/console:v3.15.0 | yes |
| temporalio/auto-setup:1.29.7 | yes |
| temporalio/ui:2.58.1 | yes |
| arizephoenix/phoenix:version-22.1.0 | yes |
| python:3.12.16-slim-bookworm | yes |
| node:22.21.1-alpine3.22 | yes |
| ghcr.io/astral-sh/uv:0.13.10 | yes |

## Consequences

Upgrades are explicit PRs that re-run `make check-images`.
