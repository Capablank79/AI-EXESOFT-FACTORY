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
from infrastructure.services.consistency import ConcurrencyError


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
    version: int = 0  # Versionado optimista
    idempotency_key: Optional[str] = None  # Clave de idempotencia para operaciones idempotentes


@dataclass
class TaskDomain:
    task_id: uuid.UUID
    domain: str
    spec: dict
    spec_hash: str
    updated_at: Optional[datetime] = None
    version: int = 0  # Versionado optimista


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
        created_by: Optional[uuid.UUID] = None,
        idempotency_key: Optional[str] = None,
        task_id: Optional[uuid.UUID] = None,
        max_attempts: int = 3
    ) -> Task:
        """Crear una nueva tarea (idempotente por idempotency_key opcional)."""
        # Serialize plan-definition edits with approval and execution claims.
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM projects WHERE id = %s AND tenant_id = %s FOR UPDATE",
                    (str(project_id), str(tenant_id)),
                )
                if cur.fetchone() is None:
                    raise ValueError('Project not found in the task tenant')
        query = """
        INSERT INTO tasks
        (id, tenant_id, project_id, title, description, agent, depends_on, timeout_seconds, priority, scheduled_at, created_by, idempotency_key, max_attempts)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, project_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
        DO UPDATE SET idempotency_key = tasks.idempotency_key
        WHERE tasks.title IS NOT DISTINCT FROM EXCLUDED.title
          AND tasks.description IS NOT DISTINCT FROM EXCLUDED.description
          AND tasks.agent IS NOT DISTINCT FROM EXCLUDED.agent
          AND tasks.depends_on IS NOT DISTINCT FROM EXCLUDED.depends_on
          AND tasks.timeout_seconds IS NOT DISTINCT FROM EXCLUDED.timeout_seconds
          AND tasks.priority IS NOT DISTINCT FROM EXCLUDED.priority
          AND tasks.scheduled_at IS NOT DISTINCT FROM EXCLUDED.scheduled_at
          AND tasks.created_by IS NOT DISTINCT FROM EXCLUDED.created_by
        RETURNING *
        """
        params = [
            str(task_id) if task_id else None,
            str(tenant_id), str(project_id), title, description, agent,
            depends_on, timeout_seconds, priority,
            scheduled_at.isoformat() if scheduled_at else None,
            str(created_by) if created_by else None,
            idempotency_key, max_attempts
        ]

        row = self._fetch_one(query, params)
        if row is None:
            raise ValueError("Idempotency key reused with different task data")
        return Task(**row)

    def cancel_pending_project_tasks(self, project_id: uuid.UUID) -> int:
        """Retire unstarted tasks when an explicitly authorized plan is replaced."""
        if self.tenant_id is None:
            raise ValueError('tenant_id is required to replace project tasks')
        query = """UPDATE tasks
                      SET status = 'cancelled', finished_at = NOW(),
                          version = version + 1, updated_at = NOW()
                    WHERE project_id = %s AND status = 'pending'"""
        return self._execute(query, [str(project_id)])

    def get_task(self, task_id: uuid.UUID) -> Optional[Task]:
        """Obtener tarea por ID."""
        row = self._fetch_one("SELECT * FROM tasks WHERE id = %s", [str(task_id)])
        return Task(**row) if row else None

    def update_task_status(
        self,
        task_id: uuid.UUID,
        status: str,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        expected_version: Optional[int] = None
    ) -> bool:
        """Actualizar estado de la tarea con validación de transición y control de concurrencia optimista."""
        # Validar transición de estado
        from infrastructure.services.consistency import VALID_TASK_TRANSITIONS, StateTransitionError

        current_task = self.get_task(task_id)
        if not current_task:
            raise ValueError(f"Tarea {task_id} no encontrada")

        current_status = current_task.status
        if status not in VALID_TASK_TRANSITIONS.get(current_status, []):
            raise StateTransitionError(
                f"Transición inválida: {current_status} -> {status}. "
                f"Transiciones válidas: {VALID_TASK_TRANSITIONS.get(current_status, [])}"
            )

        # Control de concurrencia optimista
        if expected_version is not None and current_task.version != expected_version:
            raise ConcurrencyError(
                f"Versión desactualizada: esperada {expected_version}, actual {current_task.version}"
            )

        new_version = current_task.version + 1
        query = """
        UPDATE tasks
        SET status = %s,
            started_at = CASE
                WHEN %s = 'running' THEN COALESCE(%s, started_at, NOW())
                ELSE COALESCE(%s, started_at)
            END,
            finished_at = CASE
                WHEN %s = 'running' THEN NULL
                WHEN %s IN ('done', 'failed', 'cancelled') THEN COALESCE(%s, NOW())
                ELSE COALESCE(%s, finished_at)
            END,
            updated_at = NOW(), version = %s
        WHERE id = %s AND version = %s
        """
        params = [
            status, status,
            started_at.isoformat() if started_at else None,
            started_at.isoformat() if started_at else None,
            status, status,
            finished_at.isoformat() if finished_at else None,
            finished_at.isoformat() if finished_at else None,
            new_version, str(task_id), current_task.version
        ]
        if self.tenant_id is not None:
            query = query.replace("WHERE id = %s AND version = %s", "WHERE id = %s AND version = %s AND tenant_id = %s")
            params.append(str(self.tenant_id))

        rows = self._execute(query, params)
        if rows == 0:
            raise ConcurrencyError(
                f"Versión desactualizada: la tarea {task_id} fue modificada concurrentemente"
            )
        return True

    def increment_attempts(self, task_id: uuid.UUID, expected_version: Optional[int] = None) -> int:
        """Incrementar contador de intentos con control de concurrencia optimista."""
        current_task = self.get_task(task_id)
        if not current_task:
            raise ValueError(f"Tarea {task_id} no encontrada")

        if expected_version is not None and current_task.version != expected_version:
            raise ConcurrencyError(
                f"Versión desactualizada: esperada {expected_version}, actual {current_task.version}"
            )

        new_version = current_task.version + 1
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                query = "UPDATE tasks SET attempts = attempts + 1, version = %s, updated_at = NOW() WHERE id = %s AND version = %s"
                params = (new_version, str(task_id), current_task.version)
                if self.tenant_id is not None:
                    query += " AND tenant_id = %s"
                    params += (str(self.tenant_id),)
                query += " RETURNING attempts, version"
                cur.execute(
                    query, params
                )
                row = cur.fetchone()
                if not row:
                    raise ConcurrencyError(
                        f"Versión desactualizada: la tarea {task_id} fue modificada concurrentemente"
                    )
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

    def list_running_tasks(self, project_id: Optional[uuid.UUID] = None) -> List[Task]:
        """List this tenant's active tasks for restart recovery."""
        query = "SELECT * FROM tasks WHERE status = 'running'"
        params = []
        if project_id is not None:
            query += " AND project_id = %s"
            params.append(str(project_id))
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
        query = """
            UPDATE tasks
            SET status = 'running', started_at = NOW(), updated_at = NOW(), version = version + 1
            WHERE id = %s AND status = 'pending'
        """
        params = [str(task_id)]
        if self.tenant_id is not None:
            query = query.replace("WHERE id = %s AND status = 'pending'", "WHERE id = %s AND status = 'pending' AND tenant_id = %s")
            params.append(str(self.tenant_id))
        rows = self._execute(
            query, params
        )
        return rows > 0

    def claim_task_for_execution(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        expected_version: int,
    ) -> Optional[Task]:
        """Atomically claim a pending task within this repository's tenant scope.

        Attempts and version advance in the same conditional update as the status,
        so competing workers cannot both dispatch the same task.
        """
        if self.tenant_id is None:
            raise ValueError("tenant_id is required to claim a task")
        # Enforce at the repository boundary as well as the Runtime boundary so
        # direct orchestration/worker callers cannot bypass operator approval.
        from infrastructure.plan_approval import require_plan_approval
        with self._get_connection() as approval_conn:
            require_plan_approval(approval_conn, self.tenant_id, project_id)
        query = """
            UPDATE tasks
            SET status = 'running', started_at = NOW(), finished_at = NULL,
                attempts = attempts + 1, version = version + 1, updated_at = NOW()
            WHERE id = %s AND project_id = %s AND tenant_id = %s
              AND status = 'pending' AND version = %s AND attempts < max_attempts
            RETURNING *
        """
        with self._get_connection() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(query, (str(task_id), str(project_id), str(self.tenant_id), expected_version))
                row = cur.fetchone()
        return Task(**row) if row else None

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
