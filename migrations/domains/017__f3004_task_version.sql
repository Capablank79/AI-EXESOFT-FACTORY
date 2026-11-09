-- EXF-F3-004: optimistic task versioning.
-- Dependency: 016__f3004_idempotency_constraints.sql
-- Applied by migrations.migration_engine only after operator approval.

ALTER TABLE tasks
    ADD COLUMN version INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN tasks.version IS
    'Optimistic concurrency version; incremented on each task state or attempt update';
