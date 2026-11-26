# syntax=docker/dockerfile:1.7
# One Python image for api, worker, ingestion, indexing and evals.
FROM ghcr.io/astral-sh/uv:0.13.10 AS uv

FROM python:3.12.16-slim-bookworm
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY common ./common
COPY ingestion ./ingestion
COPY indexing ./indexing
COPY agent ./agent
COPY workflows ./workflows
COPY api ./api
COPY evals ./evals
COPY tests/fixtures ./tests/fixtures
RUN useradd --create-home --uid 10001 app && mkdir -p /models /app/docs/eval && \
    chown -R app /models /app/docs
USER app
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
