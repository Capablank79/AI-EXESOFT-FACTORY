-- ============================================================
-- 005__execution.sql
-- Estado: MIGRATING
-- Checksum: 5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3
-- Dependencia: 004__planning.sql
-- ============================================================

-- Tabla de corridas de tareas (registro de ejecuciones)
CREATE TABLE IF NOT EXISTS task_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    duration_ms BIGINT GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000
    ) STORED,
    summary TEXT,
    evidence TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para ejecución
CREATE INDEX IF NOT EXISTS idx_task_runs_tenant ON task_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_project ON task_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
CREATE INDEX IF NOT EXISTS idx_task_runs_agent ON task_runs(agent_name);
CREATE INDEX IF NOT EXISTS idx_task_runs_started ON task_runs(started_at);

-- Tabla de estado del orquestador por proyecto
CREATE TABLE IF NOT EXISTS orchestrator_state (
    project_id UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    current_state TEXT NOT NULL DEFAULT 'initial',
    last_event TEXT,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checkpoint JSONB -- Estado de recuperación
);

-- Comentarios
COMMENT ON TABLE task_runs IS 'Historial de ejecuciones de tareas por agentes';
COMMENT ON COLUMN task_runs.evidence IS 'Evidencias verificables (hashes de artefactos, IDs de pruebas, etc.)';
