-- ============================================================
-- 003__projects.sql
-- Estado: MIGRATING
-- Checksum: 7e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3
-- Dependencia: 002__identity.sql
-- ============================================================

-- Tabla de proyectos
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT,
    request JSONB NOT NULL, -- Petición inicial del proyecto
    spec_url TEXT, -- URL o ruta al spec del proyecto
    status domain_project_status NOT NULL DEFAULT 'created',
    priority INTEGER CHECK (priority BETWEEN 1 AND 10),
    owner_user_id UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, slug),
    UNIQUE(tenant_id, name)
);

-- Índices para proyectos
CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug);

-- Tabla de especificaciones de proyectos (ODD)
CREATE TABLE IF NOT EXISTS project_specs (
    project_id UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    spec JSONB NOT NULL, -- Documento ODD completo
    spec_hash TEXT NOT NULL, -- Hash SHA-256 para detección de cambios
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice para búsqueda en specs
CREATE INDEX IF NOT EXISTS idx_project_specs_hash ON project_specs(spec_hash);

-- Tabla de snapshots históricos (inmutables)
CREATE TABLE IF NOT EXISTS project_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    snapshot JSONB NOT NULL,
    reason TEXT, -- Por qué se tomó el snapshot
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para snapshots
CREATE INDEX IF NOT EXISTS idx_project_snapshots_project ON project_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_project_snapshots_created ON project_snapshots(created_at);

-- Comentarios
COMMENT ON TABLE projects IS 'Proyectos de ingeniería de software';
COMMENT ON COLUMN projects.request IS 'Petición inicial en formato JSONB (requisitos, criterios, etc.)';