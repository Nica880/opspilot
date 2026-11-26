-- OpsPilot core schema. Applied by `python -m common.migrate`.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repos (
    id              BIGSERIAL PRIMARY KEY,
    full_name       TEXT NOT NULL UNIQUE,          -- "owner/name"
    license_spdx    TEXT,                          -- recorded for every ingested repo
    default_branch  TEXT,
    html_url        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS issues (
    id              BIGSERIAL PRIMARY KEY,
    repo_id         BIGINT NOT NULL REFERENCES repos(id),
    number          INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('issue', 'pull_request', 'discussion')),
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL,
    state_reason    TEXT,
    author          TEXT,
    labels          TEXT[] NOT NULL DEFAULT '{}',
    html_url        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    closed_at       TIMESTAMPTZ,
    comments_count  INTEGER NOT NULL DEFAULT 0,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo_id, number)
);
CREATE INDEX IF NOT EXISTS issues_repo_created_idx ON issues (repo_id, created_at);
CREATE INDEX IF NOT EXISTS issues_updated_idx ON issues (updated_at);

CREATE TABLE IF NOT EXISTS comments (
    id              BIGINT PRIMARY KEY,            -- GitHub comment id
    issue_id        BIGINT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    author          TEXT,
    author_association TEXT,
    body            TEXT NOT NULL DEFAULT '',
    html_url        TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_issue_idx ON comments (issue_id, created_at);

CREATE TABLE IF NOT EXISTS docs_pages (
    id              BIGSERIAL PRIMARY KEY,
    repo_id         BIGINT NOT NULL REFERENCES repos(id),
    path            TEXT NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    sha             TEXT NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS ingest_cursors (
    repo_id         BIGINT NOT NULL REFERENCES repos(id),
    resource        TEXT NOT NULL,                 -- issues | comments | docs
    since           TIMESTAMPTZ,                   -- incremental high-water mark
    etag            TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (repo_id, resource)
);

-- HTTP cache for conditional requests (ETag / Last-Modified); 304s are free against the rate limit.
CREATE TABLE IF NOT EXISTS http_cache (
    url             TEXT PRIMARY KEY,
    etag            TEXT,
    last_modified   TEXT,
    body            JSONB NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS triage_runs (
    id              UUID PRIMARY KEY,
    issue_id        BIGINT NOT NULL REFERENCES issues(id),
    workflow_id     TEXT,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    retrieval_variant TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('running', 'awaiting_approval', 'approved',
                                                    'rejected', 'applied', 'failed', 'needs_human')),
    steps           JSONB NOT NULL DEFAULT '[]',
    error           TEXT,
    latency_ms      INTEGER,
    trace_id        TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS triage_runs_issue_idx ON triage_runs (issue_id);
CREATE INDEX IF NOT EXISTS triage_runs_status_idx ON triage_runs (status);

CREATE TABLE IF NOT EXISTS suggestions (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES triage_runs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('classification', 'labels', 'duplicates',
                                                  'draft', 'repro_checklist', 'verification')),
    payload         JSONB NOT NULL,
    confidence      REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS suggestions_run_idx ON suggestions (run_id);

CREATE TABLE IF NOT EXISTS approvals (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES triage_runs(id) ON DELETE CASCADE,
    decision        TEXT NOT NULL CHECK (decision IN ('approve', 'edit', 'reject')),
    reviewer        TEXT NOT NULL,
    edited_payload  JSONB,
    note            TEXT,
    applied_url     TEXT,                          -- link to the sandbox-repo action, if any
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ground_truth (
    id              BIGSERIAL PRIMARY KEY,
    issue_id        BIGINT NOT NULL REFERENCES issues(id),
    source          TEXT NOT NULL CHECK (source IN ('maintainer_labels', 'duplicate_of',
                                                    'linked_answer', 'human_edit')),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issue_id, source)
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id              UUID PRIMARY KEY,
    suite           TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    retrieval_variant TEXT NOT NULL,
    cutoff          TIMESTAMPTZ,
    n_cases         INTEGER NOT NULL,
    metrics         JSONB NOT NULL,
    git_sha         TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS redteam_cases (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    expectation     JSONB NOT NULL,
    last_passed     BOOLEAN,
    last_run_at     TIMESTAMPTZ
);
