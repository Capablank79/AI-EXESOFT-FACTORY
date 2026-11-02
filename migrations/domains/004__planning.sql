-- ============================================================
-- 004__planning.sql
-- Estado: MIGRATING
-- Checksum: 6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4
-- Dependencia: 003__projects.sql
-- ============================================================

-- Tabla de tareas
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    agent TEXT NOT NULL, -- Nombre del agente que ejecutará
    status domain_task_status NOT NULL DEFAULT 'pending',
    depends_on TEXT[], -- IDs de tareas dependientes
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    timeout_seconds INTEGER CHECK (timeout_seconds > 0),
    priority INTEGER CHECK (priority BETWEEN 1 AND 10),
    scheduled_at TIMESTAMPTZ, -- Para programación diferida
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR
        (status != 'running' AND (finished_at IS NULL OR finished_at IS NOT NULL))
    )
);

-- Índices críticos
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON tasks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled ON tasks(scheduled_at) WHERE scheduled_at IS NOT NULL;

-- Índice para dependencias (búsqueda de tareas por ID dependiente)
CREATE INDEX IF NOT EXISTS idx_tasks_depends_gin ON tasks USING GIN(depends_on);

-- Tabla de enlaces específicos de tareas (ODD por dominio)
CREATE TABLE IF NOT EXISTS task_domains (
    task_id UUID PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    domain TEXT NOT NULL CHECK (domain IN ('identity', 'projects', 'planning', 'execution', 'memory', 'events', 'quality', 'providers', 'artifacts')),
    spec JSONB NOT NULL, -- Especificación del dominio
    spec_hash TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice para dominios
CREATE INDEX IF NOT EXISTS idx_task_domains_domain ON task_domains(domain);

-- Tabla de tareas históricas (inmutables)
CREATE TABLE IF NOT EXISTS task_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    snapshot JSONB NOT NULL,
    reason TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Comentarios
COMMENT ON TABLE tasks IS 'Unidades de trabajo a ejecutar por agentes';
COMMENT ON COLUMN tasks.depends_on IS 'Lista de IDs de tareas que deben completarse primero';