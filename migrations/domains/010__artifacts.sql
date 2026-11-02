-- ============================================================
-- 010__artifacts.sql
-- Estado: MIGRATING
-- Checksum: 0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6
-- Dependencia: 009__providers.sql
-- ============================================================

-- Tabla de artefactos (archivos entregables)
CREATE TABLE IF NOT EXISTS artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    task_run_id UUID REFERENCES task_runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL, -- Ruta relativa en el workspace
    type TEXT NOT NULL CHECK (type IN ('code', 'config', 'doc', 'test', 'data', 'binary', 'spec')),
    mime_type domain_mime_type,
    size_bytes BIGINT CHECK (size_bytes >= 0),
    sha256_hash TEXT NOT NULL, -- Hash SHA-256 del contenido
    metadata JSONB, -- Metadatos adicionales
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, project_id, name, sha256_hash)
);

-- Índices críticos
CREATE INDEX IF NOT EXISTS idx_artifacts_tenant ON artifacts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(type);
CREATE INDEX IF NOT EXISTS idx_artifacts_hash ON artifacts(sha256_hash);
CREATE INDEX IF NOT EXISTS idx_artifacts_verified ON artifacts(is_verified);

-- Tabla de almacenamiento de archivos (metadata, contenido en filesystem)
CREATE TABLE IF NOT EXISTS artifact_storage (
    artifact_id UUID PRIMARY KEY REFERENCES artifacts(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL, -- Ruta física del archivo
    storage_type TEXT NOT NULL CHECK (storage_type IN ('local', 's3', 'gcs', 'azure')),
    last_accessed TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para storage
CREATE INDEX IF NOT EXISTS idx_artifact_storage_type ON artifact_storage(storage_type);

-- Tabla de relaciones entre artefactos (dependencias)
CREATE TABLE IF NOT EXISTS artifact_dependencies (
    parent_artifact_id UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    child_artifact_id UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (parent_artifact_id, child_artifact_id)
);

-- Comentarios
COMMENT ON TABLE artifacts IS 'Artefactos entregables con hash de verificación';
COMMENT ON COLUMN artifacts.sha256_hash IS 'Hash SHA-256 usado para Quality Gate de validación';