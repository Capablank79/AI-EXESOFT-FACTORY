-- EXF-F3-004: operation-scoped idempotency keys (PostgreSQL 18)
-- Dependency: 015__fts_odd_documents.sql
-- Applied by the versioned migration engine only; do not run manually.
-- Migration 016 is assumed NOT APPLIED (verify status before deployment).

ALTER TABLE tasks
    ADD COLUMN idempotency_key VARCHAR(255);

ALTER TABLE knowledge
    ADD COLUMN idempotency_key VARCHAR(255);

-- An absent key means the insert is not deduplicated.
-- Scope keys to the project, not business titles/content.
CREATE UNIQUE INDEX idx_tasks_idempotency_key
    ON tasks (tenant_id, project_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- NULLS NOT DISTINCT makes project_id=NULL meaningful for global knowledge.
CREATE UNIQUE INDEX idx_knowledge_idempotency_key
    ON knowledge (tenant_id, project_id, idempotency_key) NULLS NOT DISTINCT
    WHERE idempotency_key IS NOT NULL;

COMMENT ON COLUMN tasks.idempotency_key IS
    'Optional caller-stable key; uniqueness scoped by tenant/project';
COMMENT ON COLUMN knowledge.idempotency_key IS
    'Optional caller-stable key; uniqueness scoped by tenant/project, including NULL project';
