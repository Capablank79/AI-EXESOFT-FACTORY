-- ============================================================
-- 001__domains.sql
-- Estado: MIGRATING
-- Checksum: 3a7bd3e2358b6b7c1e2c0d1f5a9b8e7c6d3f2a1b0e9d8c7f6a5b4c3d2e1f0a9b8
-- Dependencia: 000__schema_migrations.sql
-- ============================================================

-- Dominio UUID (formato PostgreSQL 4.2+)
CREATE DOMAIN domain_uuid AS UUID
    CHECK (VALUE IS NOT NULL);

-- Dominio UUID texto (almacenamiento como texto para compatibilidad)
CREATE DOMAIN domain_uuid_text AS TEXT
    CHECK (LENGTH(VALUE) = 32 AND VALUE ~ '^[0-9a-f]{32}$');

-- Dominios de estado para tareas
CREATE DOMAIN domain_task_status AS TEXT
    CHECK (VALUE IN (
        'pending',      -- Pendiente ejecución
        'running',      -- En ejecución
        'done',         -- Completada
        'failed',       -- Fallida (máximo intentos alcanzados)
        'cancelled',    -- Cancelada
        'blocked',      -- Bloqueada por dependencias
        'interrupted'   -- Interrumpida (fallback)
    ));

-- Dominios de estado para proyectos
CREATE DOMAIN domain_project_status AS TEXT
    CHECK (VALUE IN (
        'created',      -- Proyecto creado
        'planned',      -- Planificado
        'running',      -- En ejecución
        'blocked',      -- Bloqueado
        'review',       -- En revisión
        'done',         -- Completado
        'cancelled'     -- Cancelado
    ));

-- Dominio de tipo de conocimiento (ODD)
CREATE DOMAIN domain_knowledge_kind AS TEXT
    CHECK (VALUE IN (
        'spec',         -- Especificación od
        'artifact',     -- Artefacto entregado
        'note',         -- Nota informativa
        'reference',    -- Referencia externa
        'example'       -- Ejemplo
    ));

-- Dominio de tipo de evento
CREATE DOMAIN domain_event_type AS TEXT
    CHECK (VALUE IN (
        'project.created',
        'project.status',
        'task.created',
        'task.started',
        'task.completed',
        'task.failed',
        'task.succeeded',
        'task.interrupted',
        'task.recovered',
        'agent.ran',
        'agent.completed',
        'quality.validated',
        'quality.rejected'
    ));

-- Dominio de tipo de provider
CREATE DOMAIN domain_provider_type AS TEXT
    CHECK (VALUE IN ('llm', 'embedding', 'image', 'audio', 'file'));

-- Dominio de visibilidad
CREATE DOMAIN domain_visibility AS TEXT
    CHECK (VALUE IN ('private', 'team', 'organization', 'public'));

-- Dominio MIME type
CREATE DOMAIN domain_mime_type AS TEXT
    CHECK (VALUE ~ '^[a-z]+\+[a-z0-9.+-]+$|^[a-z]+/[a-z0-9.+-]+$');

-- Dominio URL segura
CREATE DOMAIN domain_safe_url AS TEXT
    CHECK (VALUE ~ '^https?://[a-zA-Z0-9._/-]+$');

-- Comentarios
COMMENT ON DOMAIN domain_uuid IS 'UUID PostgreSQL nativo (formato 8-4-4-4-12)';
COMMENT ON DOMAIN domain_uuid_text IS 'UUID almacenado como texto (32 caracteres hex)';
COMMENT ON DOMAIN domain_task_status IS 'Estado de ciclo de vida de una tarea';
COMMENT ON DOMAIN domain_project_status IS 'Estado de ciclo de vida de un proyecto';
