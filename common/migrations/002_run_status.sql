ALTER TABLE triage_runs DROP CONSTRAINT IF EXISTS triage_runs_status_check;
ALTER TABLE triage_runs ADD CONSTRAINT triage_runs_status_check
    CHECK (status IN ('running', 'awaiting_approval', 'needs_human', 'approved', 'rejected',
                      'applied', 'failed', 'expired'));
CREATE INDEX IF NOT EXISTS triage_runs_started_idx ON triage_runs (started_at DESC);
