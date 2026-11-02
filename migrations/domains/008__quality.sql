-- ============================================================
-- 008__quality.sql
-- Estado: MIGRATING
-- Checksum: 2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c
-- Dependencia: 007__events.sql
-- ============================================================

-- Tabla de puertas de calidad (Quality Gates)
CREATE TABLE IF NOT EXISTS quality_gates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('test_coverage', 'security_scan', 'lint', 'custom')),
    config JSONB NOT NULL, -- Configuración del gate
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para quality gates
CREATE INDEX IF NOT EXISTS idx_quality_gates_tenant ON quality_gates(tenant_id);
CREATE INDEX IF NOT EXISTS idx_quality_gates_type ON quality_gates(type);

-- Tabla de validaciones ejecutadas
CREATE TABLE IF NOT EXISTS validations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    quality_gate_id UUID REFERENCES quality_gates(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed', 'skipped')),
    evidence TEXT[], -- Hashes de evidencias
    metrics JSONB, -- Métricas específicas (cobertura, errores, etc.)
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para validaciones
CREATE INDEX IF NOT EXISTS idx_validations_tenant ON validations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_validations_project ON validations(project_id);
CREATE INDEX IF NOT EXISTS idx_validations_status ON validations(status);
CREATE INDEX IF NOT EXISTS idx_validations_gate ON validations(quality_gate_id);

-- Tabla de evidencias verificables
CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    task_run_id UUID REFERENCES task_runs(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('test_result', 'build_log', 'security_report', 'file_hash', 'custom')),
    hash TEXT NOT NULL, -- SHA-256 del archivo
    url TEXT, -- Ruta o URL del archivo
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para evidence
CREATE INDEX IF NOT EXISTS idx_evidence_tenant ON evidence(tenant_id);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence(type);
CREATE INDEX IF NOT EXISTS idx_evidence_hash ON evidence(hash);

-- Comentarios
COMMENT ON TABLE quality_gates IS 'Definición de puertas de calidad para validación';
COMMENT ON TABLE validations IS 'Ejecución de validaciones contra quality gates';
COMMENT ON TABLE evidence IS 'Evidencias verificables vinculadas a ejecuciones';