-- ============================================================
-- 012__integrity_security.sql
-- EXF-F1-003: tenant integrity, JSONB indexes and least privilege
-- Dependencia: 011__indexes.sql
-- ============================================================

-- Tenant-owned principals and projects must be scoped.
ALTER TABLE users ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE agent_identities ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE projects ALTER COLUMN tenant_id SET NOT NULL;

-- Candidate keys used to enforce tenant-consistent relationships.
ALTER TABLE users ADD CONSTRAINT users_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE agent_identities ADD CONSTRAINT agent_identities_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE projects ADD CONSTRAINT projects_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE tasks ADD CONSTRAINT tasks_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE tasks ADD CONSTRAINT tasks_tenant_project_id_id_key UNIQUE (tenant_id, project_id, id);
ALTER TABLE task_runs ADD CONSTRAINT task_runs_tenant_id_id_key UNIQUE (tenant_id, id);
ALTER TABLE task_runs ADD CONSTRAINT task_runs_tenant_project_id_id_key UNIQUE (tenant_id, project_id, id);

-- Tenant-consistent foreign keys across the project and execution domains.
ALTER TABLE projects ADD CONSTRAINT projects_owner_same_tenant_fk
    FOREIGN KEY (tenant_id, owner_user_id) REFERENCES users (tenant_id, id);
ALTER TABLE tasks ADD CONSTRAINT tasks_project_same_tenant_fk
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE task_runs ADD CONSTRAINT task_runs_task_same_project_tenant_fk
    FOREIGN KEY (tenant_id, project_id, task_id)
    REFERENCES tasks (tenant_id, project_id, id) ON DELETE CASCADE;

ALTER TABLE knowledge ADD CONSTRAINT knowledge_project_same_tenant_fk
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE knowledge ADD CONSTRAINT knowledge_project_requires_tenant_ck
    CHECK (project_id IS NULL OR tenant_id IS NOT NULL);

ALTER TABLE events ADD CONSTRAINT events_project_same_tenant_fk
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id)
    ON DELETE SET NULL (project_id);
ALTER TABLE events ADD CONSTRAINT events_task_same_project_tenant_fk
    FOREIGN KEY (tenant_id, project_id, task_id)
    REFERENCES tasks (tenant_id, project_id, id) ON DELETE SET NULL (task_id);
ALTER TABLE events ADD CONSTRAINT events_project_requires_tenant_ck
    CHECK (project_id IS NULL OR tenant_id IS NOT NULL);

ALTER TABLE validations ADD CONSTRAINT validations_project_same_tenant_fk
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE validations ADD CONSTRAINT validations_task_same_project_tenant_fk
    FOREIGN KEY (tenant_id, project_id, task_id)
    REFERENCES tasks (tenant_id, project_id, id) ON DELETE CASCADE;
ALTER TABLE validations ADD CONSTRAINT validations_project_requires_tenant_ck
    CHECK (project_id IS NULL OR tenant_id IS NOT NULL);
ALTER TABLE validations ADD CONSTRAINT validations_task_requires_project_ck
    CHECK (task_id IS NULL OR project_id IS NOT NULL);

ALTER TABLE artifacts ADD CONSTRAINT artifacts_project_same_tenant_fk
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_run_same_project_tenant_fk
    FOREIGN KEY (tenant_id, project_id, task_run_id)
    REFERENCES task_runs (tenant_id, project_id, id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_project_requires_tenant_ck
    CHECK (project_id IS NULL OR tenant_id IS NOT NULL);
ALTER TABLE artifacts ADD CONSTRAINT artifacts_run_requires_project_ck
    CHECK (task_run_id IS NULL OR project_id IS NOT NULL);

ALTER TABLE evidence ADD CONSTRAINT evidence_project_same_tenant_fk
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE evidence ADD CONSTRAINT evidence_run_same_project_tenant_fk
    FOREIGN KEY (tenant_id, project_id, task_run_id)
    REFERENCES task_runs (tenant_id, project_id, id) ON DELETE CASCADE;
ALTER TABLE evidence ADD CONSTRAINT evidence_project_requires_tenant_ck
    CHECK (project_id IS NULL OR tenant_id IS NOT NULL);
ALTER TABLE evidence ADD CONSTRAINT evidence_run_requires_project_ck
    CHECK (task_run_id IS NULL OR project_id IS NOT NULL);

ALTER TABLE model_requests ADD CONSTRAINT model_requests_task_same_tenant_fk
    FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE model_requests ADD CONSTRAINT model_requests_task_requires_tenant_ck
    CHECK (task_id IS NULL OR tenant_id IS NOT NULL);

-- Structured JSONB documents must remain JSON objects.
ALTER TABLE agent_identities ADD CONSTRAINT agent_identities_config_object_ck
    CHECK (config IS NULL OR jsonb_typeof(config) = 'object');
ALTER TABLE projects ADD CONSTRAINT projects_request_object_ck
    CHECK (jsonb_typeof(request) = 'object');
ALTER TABLE project_specs ADD CONSTRAINT project_specs_spec_object_ck
    CHECK (jsonb_typeof(spec) = 'object');
ALTER TABLE project_specs ADD CONSTRAINT project_specs_hash_sha256_ck
    CHECK (spec_hash ~ '^[0-9a-f]{64}$');
ALTER TABLE project_snapshots ADD CONSTRAINT project_snapshots_object_ck
    CHECK (jsonb_typeof(snapshot) = 'object');
ALTER TABLE task_domains ADD CONSTRAINT task_domains_spec_object_ck
    CHECK (jsonb_typeof(spec) = 'object');
ALTER TABLE task_domains ADD CONSTRAINT task_domains_hash_sha256_ck
    CHECK (spec_hash ~ '^[0-9a-f]{64}$');
ALTER TABLE task_snapshots ADD CONSTRAINT task_snapshots_object_ck
    CHECK (jsonb_typeof(snapshot) = 'object');
ALTER TABLE orchestrator_state ADD CONSTRAINT orchestrator_state_checkpoint_object_ck
    CHECK (checkpoint IS NULL OR jsonb_typeof(checkpoint) = 'object');
ALTER TABLE task_runs ADD CONSTRAINT task_runs_metadata_object_ck
    CHECK (metadata IS NULL OR jsonb_typeof(metadata) = 'object');
ALTER TABLE events ADD CONSTRAINT events_detail_object_ck
    CHECK (jsonb_typeof(detail) = 'object');
ALTER TABLE quality_gates ADD CONSTRAINT quality_gates_config_object_ck
    CHECK (jsonb_typeof(config) = 'object');
ALTER TABLE validations ADD CONSTRAINT validations_metrics_object_ck
    CHECK (metrics IS NULL OR jsonb_typeof(metrics) = 'object');
ALTER TABLE evidence ADD CONSTRAINT evidence_metadata_object_ck
    CHECK (metadata IS NULL OR jsonb_typeof(metadata) = 'object');
ALTER TABLE evidence ADD CONSTRAINT evidence_hash_sha256_ck
    CHECK (hash ~ '^[0-9a-f]{64}$');
ALTER TABLE providers ADD CONSTRAINT providers_config_object_ck
    CHECK (config IS NULL OR jsonb_typeof(config) = 'object');
ALTER TABLE artifacts ADD CONSTRAINT artifacts_metadata_object_ck
    CHECK (metadata IS NULL OR jsonb_typeof(metadata) = 'object');
ALTER TABLE artifacts ADD CONSTRAINT artifacts_hash_sha256_ck
    CHECK (sha256_hash ~ '^[0-9a-f]{64}$');

-- Query indexes for composite foreign keys and JSONB containment/search.
CREATE INDEX idx_projects_tenant_owner ON projects (tenant_id, owner_user_id);
CREATE INDEX idx_tasks_tenant_project ON tasks (tenant_id, project_id);
CREATE INDEX idx_task_runs_tenant_project_task ON task_runs (tenant_id, project_id, task_id);
CREATE INDEX idx_knowledge_tenant_project ON knowledge (tenant_id, project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_events_tenant_project_task ON events (tenant_id, project_id, task_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_validations_tenant_project_task ON validations (tenant_id, project_id, task_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_artifacts_tenant_project_run ON artifacts (tenant_id, project_id, task_run_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_evidence_tenant_project_run ON evidence (tenant_id, project_id, task_run_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_model_requests_tenant_task ON model_requests (tenant_id, task_id) WHERE task_id IS NOT NULL;

CREATE INDEX idx_agent_identities_config_gin ON agent_identities USING GIN (config);
CREATE INDEX idx_projects_request_gin ON projects USING GIN (request);
CREATE INDEX idx_project_specs_spec_gin ON project_specs USING GIN (spec);
CREATE INDEX idx_project_snapshots_snapshot_gin ON project_snapshots USING GIN (snapshot);
CREATE INDEX idx_task_domains_spec_gin ON task_domains USING GIN (spec);
CREATE INDEX idx_task_snapshots_snapshot_gin ON task_snapshots USING GIN (snapshot);
CREATE INDEX idx_orchestrator_checkpoint_gin ON orchestrator_state USING GIN (checkpoint);
CREATE INDEX idx_task_runs_metadata_gin ON task_runs USING GIN (metadata);
CREATE INDEX idx_quality_gates_config_gin ON quality_gates USING GIN (config);
CREATE INDEX idx_validations_metrics_gin ON validations USING GIN (metrics);
CREATE INDEX idx_evidence_metadata_gin ON evidence USING GIN (metadata);
CREATE INDEX idx_providers_config_gin ON providers USING GIN (config);
CREATE INDEX idx_artifacts_metadata_gin ON artifacts USING GIN (metadata);

-- Application may perform DML, but may not create or alter objects.
GRANT USAGE ON SCHEMA public TO exesoft_factory_app;
REVOKE CREATE ON SCHEMA public FROM exesoft_factory_app;
GRANT USAGE, CREATE ON SCHEMA public TO exesoft_factory_migrator;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM exesoft_factory_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO exesoft_factory_app;
REVOKE ALL PRIVILEGES ON TABLE public.schema_migrations FROM exesoft_factory_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM exesoft_factory_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO exesoft_factory_app;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.odd_get_version(uuid, timestamptz) TO exesoft_factory_app;
GRANT EXECUTE ON FUNCTION public.project_stats(uuid) TO exesoft_factory_app;

-- Apply the same least-privilege policy to future objects created by migrator.
ALTER DEFAULT PRIVILEGES FOR ROLE exesoft_factory_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO exesoft_factory_app;
ALTER DEFAULT PRIVILEGES FOR ROLE exesoft_factory_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO exesoft_factory_app;
ALTER DEFAULT PRIVILEGES FOR ROLE exesoft_factory_migrator IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
