-- Extra databases on the single Postgres instance (ADR-001: one database server to operate).
-- Temporal's auto-setup creates its own `temporal` and `temporal_visibility` databases.
CREATE DATABASE phoenix OWNER opspilot;
