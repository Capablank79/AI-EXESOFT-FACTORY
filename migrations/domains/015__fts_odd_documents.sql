-- ============================================================
-- 015__fts_odd_documents.sql
-- Estado: MIGRATING
-- Descripción: Índice FTS para documentos ODD (odd_documents.body)
-- Dependencia: 014__tenant_edge_guards.sql
-- ============================================================

-- Índice GIN para búsqueda textual en body de documentos ODD
-- Usar paréntesis alrededor de la concatenación de tsvectors
CREATE INDEX IF NOT EXISTS idx_odd_documents_fts
    ON odd_documents
    USING GIN (
        (to_tsvector('spanish', body) || to_tsvector('english', body))
    );

-- Índice adicional para búsqueda combinada palabra por palabra
CREATE INDEX IF NOT EXISTS idx_odd_documents_fts_simple
    ON odd_documents
    USING GIN (to_tsvector('simple', body));

-- Comentario
COMMENT ON INDEX idx_odd_documents_fts IS 'Índice FTS para búsqueda en documentos ODD (Español y English)';
COMMENT ON INDEX idx_odd_documents_fts_simple IS 'Índice FTS simple para búsquedas rápidas';