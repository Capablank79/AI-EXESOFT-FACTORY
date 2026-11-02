-- ============================================================
-- 006__memory.sql
-- Estado: MIGRATING
-- Checksum: 4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2
-- Dependencia: 005__execution.sql
-- ============================================================

-- Tabla de conocimiento (Brain)
CREATE TABLE IF NOT EXISTS knowledge (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('spec', 'artifact', 'note', 'reference', 'example', 'conversation', 'tool_usage')),
    title TEXT NOT NULL,
    body TEXT NOT NULL, -- Contenido completo
    source TEXT NOT NULL, -- Quién/proceso creó el registro
    tags TEXT[], -- Tags para categorización
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices para búsqueda
CREATE INDEX IF NOT EXISTS idx_knowledge_tenant ON knowledge(tenant_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON knowledge(kind);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge(source);
CREATE INDEX IF NOT EXISTS idx_knowledge_gin_tags ON knowledge USING GIN(tags);

-- Índice de texto completo (PostgreSQL FTS)
CREATE INDEX IF NOT EXISTS idx_knowledge_fts ON knowledge USING GIN(
    to_tsvector('english', title || ' ' || COALESCE(body, ''))
);

-- Tabla de especificaciones ODD (versiones históricas)
CREATE TABLE IF NOT EXISTS odd_documents (
    project_id UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    body TEXT NOT NULL, -- Última versión vigente
    last_updated TEXT NOT NULL, -- Timestamp del último cambio
    updated_by UUID REFERENCES users(id)
);

-- Tabla de snapshots históricos de ODD (inmutables)
CREATE TABLE IF NOT EXISTS odd_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    doc_id UUID NOT NULL, -- Referencia a versión anterior
    body TEXT NOT NULL,
    reason TEXT NOT NULL, -- Por qué se guardó
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(project_id, doc_id, created_at)
);

-- Índices para ODD
CREATE INDEX IF NOT EXISTS idx_odd_snapshots_project ON odd_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_odd_snapshots_created ON odd_snapshots(created_at);

-- Función para recuperar versión específica de documento
CREATE OR REPLACE FUNCTION odd_get_version(
    p_project_id UUID,
    p_timestamp TIMESTAMPTZ DEFAULT NULL
) RETURNS TABLE(id UUID, body TEXT, created_at TIMESTAMPTZ) AS $$
BEGIN
    IF p_timestamp IS NULL THEN
        RETURN QUERY
        SELECT id, body, created_at FROM odd_snapshots
        WHERE project_id = p_project_id
        ORDER BY created_at DESC
        LIMIT 1;
    ELSE
        RETURN QUERY
        SELECT id, body, created_at FROM odd_snapshots
        WHERE project_id = p_project_id AND created_at <= p_timestamp
        ORDER BY created_at DESC
        LIMIT 1;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Comentarios
COMMENT ON TABLE knowledge IS 'Memoria de trabajo, episódica y semántica del sistema';
COMMENT ON TABLE odd_documents IS 'Documento ODD vigente (versión actual)';
COMMENT ON TABLE odd_snapshots IS 'Snapshots históricos inmutables de documentos ODD';