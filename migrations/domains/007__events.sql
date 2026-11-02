-- ============================================================
-- 007__events.sql
-- Estado: MIGRATING
-- Checksum: 3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0
-- Dependencia: 006__memory.sql
-- ============================================================

-- Tabla de eventos (registro de hechos del sistema)
CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    detail JSONB NOT NULL, -- Detalles estructurados del evento
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para eventos
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_gin_detail ON events USING GIN(detail);

-- Tabla de auditoría de cambios (inmutable)
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_name TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    record_id UUID NOT NULL,
    old_data JSONB,
    new_data JSONB,
    changed_by UUID REFERENCES users(id),
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para auditoría
CREATE INDEX IF NOT EXISTS idx_audit_table_rec ON audit_log(table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_audit_changed_at ON audit_log(changed_at DESC);

-- Comentarios
COMMENT ON TABLE events IS 'Registro de eventos del sistema con JSON detallado';
COMMENT ON TABLE audit_log IS 'Log de auditoría inmutable de cambios de datos';