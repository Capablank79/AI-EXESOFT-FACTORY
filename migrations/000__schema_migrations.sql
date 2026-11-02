-- ============================================================
-- 000__schema_migrations.sql
-- Estado: MIGRATING
-- Checksum: auto-generado por motor de migraciones
-- ============================================================

-- Tabla central de registro de migraciones
-- Historial inmutable de cambios aplicados
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    description TEXT,
    applied_by TEXT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice para búsqueda rápida
CREATE INDEX IF NOT EXISTS idx_schema_migrations_at ON schema_migrations(applied_at);

-- Función para verificar checksum de migración
CREATE OR REPLACE FUNCTION migration_verify_checksum(p_version TEXT, p_expected_checksum TEXT)
RETURNS BOOLEAN AS $$
DECLARE
    v_actual_checksum TEXT;
BEGIN
    SELECT checksum INTO v_actual_checksum
    FROM schema_migrations
    WHERE version = p_version;

    RETURN v_actual_checksum = p_expected_checksum;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Función para registrar migración aplicada
CREATE OR REPLACE FUNCTION migration_register(p_version TEXT, p_checksum TEXT, p_description TEXT DEFAULT NULL)
RETURNS VOID AS $$
BEGIN
    INSERT INTO schema_migrations(version, checksum, description, applied_at)
    VALUES (p_version, p_checksum, p_description, NOW())
    ON CONFLICT (version) DO UPDATE
    SET checksum = EXCLUDED.checksum,
        description = EXCLUDED.description,
        applied_at = NOW();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Función para verificar dependencias
CREATE OR REPLACE FUNCTION migration_check_dependency(p_version TEXT, p_previous_version TEXT)
RETURNS BOOLEAN AS $$
DECLARE
    v_exists BOOLEAN;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM schema_migrations
        WHERE version = p_previous_version
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'Dependencia no satisfecha: % requiere que % esté aplicada', p_version, p_previous_version;
    END IF;

    RETURN v_exists;
END;
$$ LANGUAGE plpgsql;

-- Bloqueo de migración para concurrencia segura
-- Uso: SELECT pg_advisory_lock migration_version_hash('001__domains');
CREATE OR REPLACE FUNCTION migration_version_hash(p_version TEXT)
RETURNS BIGINT AS $$
    SELECT hashtext('migration_' || p_version || '_' || current_database());
$$ LANGUAGE SQL IMMUTABLE;

-- Comentario de tabla
COMMENT ON TABLE schema_migrations IS 'Registro histórico inmutable de migraciones aplicadas';
COMMENT ON COLUMN schema_migrations.version IS 'Identificador único de migración (ej: 001__domains)';
COMMENT ON COLUMN schema_migrations.checksum IS 'SHA-256 del contenido SQL de la migración';