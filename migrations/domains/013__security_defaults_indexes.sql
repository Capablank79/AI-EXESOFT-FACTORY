-- ============================================================
-- 013__security_defaults_indexes.sql
-- EXF-F1-003 follow-up: audit JSONB indexes and function defaults
-- Dependencia: 012__integrity_security.sql
-- ============================================================

ALTER TABLE audit_log ADD CONSTRAINT audit_log_old_data_object_ck
    CHECK (old_data IS NULL OR jsonb_typeof(old_data) = 'object');
ALTER TABLE audit_log ADD CONSTRAINT audit_log_new_data_object_ck
    CHECK (new_data IS NULL OR jsonb_typeof(new_data) = 'object');
CREATE INDEX idx_audit_log_old_data_gin ON audit_log USING GIN (old_data);
CREATE INDEX idx_audit_log_new_data_gin ON audit_log USING GIN (new_data);

-- PostgreSQL's built-in PUBLIC EXECUTE default is global; a per-schema
-- REVOKE cannot override it. Restrict future functions by this role.
ALTER DEFAULT PRIVILEGES FOR ROLE exesoft_factory_migrator
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
