-- ============================================================
-- 011__indexes.sql
-- Estado: MIGRATING
-- Checksum: 9c8b7a6f5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6
-- Dependencia: 010__artifacts.sql
-- ============================================================

-- ============================================================
-- ÍNDICES CRÍTICOS Y OPTIMIZACIÓN
-- ============================================================

-- Índices compuestos para consultas frecuentes

-- 1. Proyectos: búsqueda por tenant + estado
CREATE INDEX IF NOT EXISTS idx_projects_tenant_status ON projects(tenant_id, status);

-- 2. Tareas: búsqueda por proyecto + estado + prioridad
CREATE INDEX IF NOT EXISTS idx_tasks_proj_status_priority
    ON tasks(project_id, status, priority)
    WHERE status IN ('pending', 'running', 'blocked');

-- 3. Tareas: orden de ejecución por prioridad y dependencias
CREATE INDEX IF NOT EXISTS idx_tasks_exec_order
    ON tasks(project_id, priority DESC, scheduled_at);

-- 4. Task_runs: historial de ejecución por proyecto
CREATE INDEX IF NOT EXISTS idx_task_runs_project_status
    ON task_runs(project_id, status, started_at DESC);

-- 5. Knowledge: búsqueda por proyecto y tipo
CREATE INDEX IF NOT EXISTS idx_knowledge_proj_kind
    ON knowledge(project_id, kind)
    WHERE project_id IS NOT NULL;

-- 6. Events: timeline de proyecto
CREATE INDEX IF NOT EXISTS idx_events_proj_type_created
    ON events(project_id, type, created_at DESC)
    WHERE project_id IS NOT NULL;

-- 7. Validations: estado de calidad por proyecto
CREATE INDEX IF NOT EXISTS idx_validations_proj_status
    ON validations(project_id, status)
    WHERE project_id IS NOT NULL;

-- ============================================================
-- VISTAS UTILIZADAS
-- ============================================================

-- Vista de resumen de proyecto
CREATE OR REPLACE VIEW v_project_summary AS
SELECT
    p.id,
    p.tenant_id,
    p.name,
    p.status,
    p.priority,
    t.count_total,
    t.count_pending,
    t.count_running,
    t.count_done,
    t.count_failed,
    p.created_at,
    p.updated_at
FROM projects p
LEFT JOIN (
    SELECT
        project_id,
        COUNT(*) as count_total,
        COUNT(*) FILTER (WHERE status = 'pending') as count_pending,
        COUNT(*) FILTER (WHERE status = 'running') as count_running,
        COUNT(*) FILTER (WHERE status = 'done') as count_done,
        COUNT(*) FILTER (WHERE status = 'failed') as count_failed
    FROM tasks
    GROUP BY project_id
) t ON p.id = t.project_id;

-- Vista de tareas listas para ejecución
CREATE OR REPLACE VIEW v_tasks_ready AS
SELECT
    t.*,
    p.name as project_name
FROM tasks t
JOIN projects p ON t.project_id = p.id
WHERE t.status = 'pending'
    AND t.scheduled_at <= NOW()
    AND NOT EXISTS (
        SELECT 1 FROM tasks dep
        WHERE dep.id::TEXT = ANY(t.depends_on)
        AND dep.status != 'done'
    )
ORDER BY t.priority DESC, t.created_at;

-- Vista de métricas de calidad
CREATE OR REPLACE VIEW v_quality_metrics AS
SELECT
    p.id as project_id,
    COUNT(v.id) FILTER (WHERE v.status = 'passed') as validations_passed,
    COUNT(v.id) FILTER (WHERE v.status = 'failed') as validations_failed,
    COUNT(v.id) FILTER (WHERE v.status = 'pending') as validations_pending,
    AVG(EXTRACT(EPOCH FROM (v.finished_at - v.started_at))) as avg_validation_seconds
FROM projects p
LEFT JOIN tasks t ON p.id = t.project_id
LEFT JOIN validations v ON t.id = v.task_id
GROUP BY p.id;

-- ============================================================
-- FUNCIONES DE UTILIDAD
-- ============================================================

-- Función para obtener estadísticas de proyecto
CREATE OR REPLACE FUNCTION project_stats(p_project_id UUID)
RETURNS TABLE(
    total_tasks INTEGER,
    pending_tasks INTEGER,
    running_tasks INTEGER,
    done_tasks INTEGER,
    failed_tasks INTEGER,
    task_completion_percent REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::INTEGER as total_tasks,
        COUNT(*) FILTER (WHERE status = 'pending')::INTEGER as pending_tasks,
        COUNT(*) FILTER (WHERE status = 'running')::INTEGER as running_tasks,
        COUNT(*) FILTER (WHERE status = 'done')::INTEGER as done_tasks,
        COUNT(*) FILTER (WHERE status = 'failed')::INTEGER as failed_tasks,
        ROUND(
            (COUNT(*) FILTER (WHERE status = 'done')::REAL / NULLIF(COUNT(*), 0)) * 100,
            2
        ) as task_completion_percent
    FROM tasks
    WHERE project_id = p_project_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- CONSTRAINTS ADICIONALES
-- ============================================================

-- Restricción para evitar tareas con dependencias inválidas
ALTER TABLE tasks
ADD CONSTRAINT fk_dependencies_valid
CHECK (
    array_length(depends_on, 1) IS NULL OR
    array_length(depends_on, 1) >= 0
);

-- Restricción para eventos: task_id requiere project_id
ALTER TABLE events
ADD CONSTRAINT event_requires_project_for_task
CHECK (
    task_id IS NULL OR project_id IS NOT NULL
);

-- Comentarios
COMMENT ON VIEW v_project_summary IS 'Resumen de progreso de proyecto';
COMMENT ON VIEW v_tasks_ready IS 'Tareas listas para ejecución por agentes';
COMMENT ON VIEW v_quality_metrics IS 'Métricas de calidad por proyecto';
