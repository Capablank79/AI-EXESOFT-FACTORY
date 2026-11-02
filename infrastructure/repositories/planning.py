"""Repositorio de planificación - EXESOFT AI FACTORY.

Tablas: tasks, task_domains, task_snapshots

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
import hashlib
from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass
import psycopg
from psycopg import rows
from psycopg.types.json import Jsonb
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class Task:
    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: Optional[str] = None
    agent: str = "claude"
    status: str = "pending"
    depends_on: Optional[List[uuid.UUID]] = None
    attempts: int = 0
    max_attempts: int = 3
    timeout_seconds: Optional[int] = None
    priority: int = 5
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class TaskDomain:
    task_id: uuid.UUID
    domain: str
    spec: dict
    spec_hash: str
    updated_at: Optional[datetime] = None


@dataclass
class TaskSnapshot:
    id: uuid.UUID
    task_id: uuid.UUID
    snapshot: dict
    reason: Optional[str] = None
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None


class PlanningRepository(Repository):
    """Repository para tareas y planificación."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== TASKS ==============
    def create_task(
        self,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        title: str,
        description: Optional[str] = None,
        agent: str = "claude",
        depends_on: Optional[List[uuid.UUID]] = None,
        timeout_seconds: Optional[int] = None,
        priority: int = 5,
        scheduled_at: Optional[datetime] = None,
        created_by: Optional[uuid.UUID] = None
    ) -> Task:
        """Crear una nueva tarea."""
        query = """
        INSERT INTO tasks
        (tenant_id, project_id, title, description, agent, depends_on, timeout_seconds, priority, scheduled_at, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        params = [
            str(tenant_id), str(project_id), title, description, agent,
            depends_on, timeout_seconds, priority,
            scheduled_at.isoformat() if scheduled_at else None,
            str(created_by) if created_by else None
        ]

        row = self._fetch_one(query, params)
        return Task(**row) if row else None

    def get_task(self, task_id: uuid.UUID) -> Optional[Task]:
        """Obtener tarea por ID."""
        row = self._fetch_one("SELECT * FROM tasks WHERE id = %s", [str(task_id)])
        return Task(**row) if row else None

    def update_task_status(
        self,
        task_id: uuid.UUID,
        status: str,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None
    ) -> bool:
        """Actualizar estado de la tarea."""
        rows = self._execute(
            """
            UPDATE tasks
            SET status = %s, started_at = %s, finished_at = %s, updated_at = NOW()
            WHERE id = %s
            """,
            [status, started_at.isoformat() if started_at else None,
             finished_at.isoformat() if finished_at else None, str(task_id)]
        )
        return rows > 0

    def increment_attempts(self, task_id: uuid.UUID) -> int:
        """Incrementar contador de intentos."""
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET attempts = attempts + 1, updated_at = NOW() WHERE id = %s RETURNING attempts",
                    (str(task_id),)
                )
                row = cur.fetchone()
                return row[0] if row else 0

    def list_tasks_by_project(
        self,
        project_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Task]:
        """Listar tareas de un proyecto."""
        query = "SELECT * FROM tasks WHERE project_id = %s"
        params = [str(project_id)]
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY priority DESC, created_at ASC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(query, params)
        return [Task(**row) for row in rows]

    def get_pending_tasks(
        self,
        project_id: uuid.UUID,
        agent: Optional[str] = None
    ) -> List[Task]:
        """Obtener tareas pendientes para un agente."""
        query = """
        SELECT * FROM tasks
        WHERE project_id = %s AND status = 'pending'
        """
        params = [str(project_id)]
        if agent:
            query += " AND agent = %s"
            params.append(agent)
        query += " ORDER BY priority DESC, scheduled_at ASC NULLS FIRST, created_at ASC"

        rows = self._fetch_all(query, params)
        return [Task(**row) for row in rows]

    def claim_task(self, task_id: uuid.UUID) -> bool:
        """Marcar tarea como en ejecución (claim)."""
        rows = self._execute(
            """
            UPDATE tasks
            SET status = 'running', started_at = NOW(), updated_at = NOW()
            WHERE id = %s AND status = 'pending'
            """,
            [str(task_id)]
        )
        return rows > 0

    # ============== TASK DOMAINS ==============
    def save_task_domain(
        self,
        task_id: uuid.UUID,
        domain: str,
        spec: dict
    ) -> TaskDomain:
        """Guardar dominio de especificación de tarea."""
        spec_json = str(spec)
        spec_hash = hashlib.sha256(spec_json.encode()).hexdigest()

        query = """
        INSERT INTO task_domains (task_id, domain, spec, spec_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (task_id, domain) DO UPDATE SET
            spec = EXCLUDED.spec,
            spec_hash = EXCLUDED.spec_hash,
            updated_at = NOW()
        RETURNING *
        """
        params = [str(task_id), domain, Jsonb(spec), spec_hash]

        row = self._fetch_one(query, params)
        return TaskDomain(**row) if row else None

    # ============== TASK SNAPSHOTS ==============
    def create_snapshot(
        self,
        task_id: uuid.UUID,
        snapshot: dict,
        reason: Optional[str] = None,
        created_by: Optional[uuid.UUID] = None
    ) -> TaskSnapshot:
        """Crear snapshot de tarea."""
        query = """
        INSERT INTO task_snapshots (task_id, snapshot, reason, created_by)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """
        params = [str(task_id), Jsonb(snapshot), reason, str(created_by) if created_by else None]

        row = self._fetch_one(query, params)
        return TaskSnapshot(**row) if row else None
