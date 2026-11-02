"""Repositorio de ejecución - EXESOFT AI FACTORY.

Tablas: task_runs, orchestrator_state

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass
import psycopg
from psycopg import rows
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class TaskRun:
    id: uuid.UUID
    tenant_id: uuid.UUID
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_name: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    summary: Optional[str] = None
    evidence: Optional[List[str]] = None
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None


@dataclass
class OrchestratorState:
    project_id: uuid.UUID
    current_state: str
    last_event: Optional[str] = None
    last_updated: Optional[datetime] = None
    checkpoint: Optional[dict] = None


class ExecutionRepository(Repository):
    """Repository para ejecución de tareas y estado del orquestador."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== TASK RUNS ==============
    def create_task_run(
        self,
        tenant_id: uuid.UUID,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        agent_name: str
    ) -> TaskRun:
        """Iniciar una corrida de tarea."""
        query = """
        INSERT INTO task_runs (tenant_id, task_id, project_id, agent_name, status, started_at)
        VALUES (%s, %s, %s, %s, 'running', NOW())
        RETURNING *
        """
        params = [str(tenant_id), str(task_id), str(project_id), agent_name]

        row = self._fetch_one(query, params)
        return TaskRun(**row) if row else None

    def complete_task_run(
        self,
        task_run_id: uuid.UUID,
        status: str,
        summary: Optional[str] = None,
        evidence: Optional[List[str]] = None,
        metrics: Optional[dict] = None
    ) -> TaskRun:
        """Completar corrida de tarea."""
        query = """
        UPDATE task_runs
        SET status = %s, finished_at = NOW(), summary = %s, evidence = %s, metadata = %s
        WHERE id = %s
        RETURNING *
        """
        params = [status, summary, evidence or [], metrics, str(task_run_id)]

        row = self._fetch_one(query, params)
        return TaskRun(**row) if row else None

    def get_task_run(self, task_run_id: uuid.UUID) -> Optional[TaskRun]:
        """Obtener corrida de tarea por ID."""
        row = self._fetch_one("SELECT * FROM task_runs WHERE id = %s", [str(task_run_id)])
        return TaskRun(**row) if row else None

    def list_task_runs(
        self,
        project_id: uuid.UUID,
        status: Optional[str] = None,
        agent_name: Optional[str] = None,
        limit: int = 100
    ) -> List[TaskRun]:
        """Listar corridas de tarea de un proyecto."""
        query = "SELECT * FROM task_runs WHERE project_id = %s"
        params = [str(project_id)]
        if status:
            query += " AND status = %s"
            params.append(status)
        if agent_name:
            query += " AND agent_name = %s"
            params.append(agent_name)
        query += " ORDER BY started_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(query, params)
        return [TaskRun(**row) for row in rows]

    # ============== ORCHESTRATOR STATE ==============
    def get_orchestrator_state(self, project_id: uuid.UUID) -> Optional[OrchestratorState]:
        """Obtener estado del orquestador para un proyecto."""
        row = self._fetch_one(
            "SELECT * FROM orchestrator_state WHERE project_id = %s",
            [str(project_id)]
        )
        return OrchestratorState(**row) if row else None

    def update_orchestrator_state(
        self,
        project_id: uuid.UUID,
        current_state: str,
        last_event: Optional[str] = None,
        checkpoint: Optional[dict] = None
    ) -> OrchestratorState:
        """Actualizar estado del orquestador."""
        query = """
        INSERT INTO orchestrator_state (project_id, current_state, last_event, checkpoint)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (project_id) DO UPDATE SET
            current_state = EXCLUDED.current_state,
            last_event = EXCLUDED.last_event,
            last_updated = NOW(),
            checkpoint = EXCLUDED.checkpoint
        RETURNING *
        """
        params = [str(project_id), current_state, last_event, checkpoint]

        row = self._fetch_one(query, params)
        return OrchestratorState(**row) if row else None

    def get_recent_runs(
        self,
        tenant_id: uuid.UUID,
        hours: int = 24,
        limit: int = 50
    ) -> List[TaskRun]:
        """Obtener corridas recientes de un tenant."""
        rows = self._fetch_all(
            """
            SELECT * FROM task_runs
            WHERE tenant_id = %s AND started_at > NOW() - INTERVAL '%s hours'
            ORDER BY started_at DESC
            LIMIT %s
            """,
            [str(tenant_id), hours, limit]
        )
        return [TaskRun(**row) for row in rows]