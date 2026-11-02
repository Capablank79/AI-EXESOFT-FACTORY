"""Repositorio de eventos - EXESOFT AI FACTORY.

Tablas: events, audit_log

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
class Event:
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    task_id: Optional[uuid.UUID]
    type: str
    detail: dict
    created_at: Optional[datetime] = None


@dataclass
class AuditLog:
    id: uuid.UUID
    table_name: str
    operation: str
    record_id: uuid.UUID
    old_data: Optional[dict]
    new_data: Optional[dict]
    changed_by: Optional[uuid.UUID]
    changed_at: Optional[datetime] = None


class EventsRepository(Repository):
    """Repository para eventos del sistema y auditoría."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== EVENTS ==============
    def emit_event(
        self,
        type: str,
        detail: dict,
        tenant_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        task_id: Optional[uuid.UUID] = None
    ) -> Event:
        """Emitir un evento del sistema."""
        query = """
        INSERT INTO events (tenant_id, project_id, task_id, type, detail)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """
        params = [
            str(tenant_id) if tenant_id else None,
            str(project_id) if project_id else None,
            str(task_id) if task_id else None,
            type, detail
        ]

        row = self._fetch_one(query, params)
        return Event(**row) if row else None

    def get_event(self, event_id: uuid.UUID) -> Optional[Event]:
        """Obtener evento por ID."""
        row = self._fetch_one("SELECT * FROM events WHERE id = %s", [str(event_id)])
        return Event(**row) if row else None

    def list_events(
        self,
        event_type: Optional[str] = None,
        project_id: Optional[uuid.UUID] = None,
        task_id: Optional[uuid.UUID] = None,
        limit: int = 100
    ) -> List[Event]:
        """Listar eventos con filtros."""
        base = "SELECT * FROM events"
        params = []
        conditions = []

        if event_type:
            conditions.append("type = %s")
            params.append(event_type)
        if project_id:
            conditions.append("project_id = %s")
            params.append(str(project_id))
        if task_id:
            conditions.append("task_id = %s")
            params.append(str(task_id))

        if conditions:
            base += " WHERE " + " AND ".join(conditions)

        base += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(base, params)
        return [Event(**row) for row in rows]

    def get_events_since(
        self,
        since: datetime,
        event_type: Optional[str] = None,
        project_id: Optional[uuid.UUID] = None
    ) -> List[Event]:
        """Obtener eventos desde un timestamp."""
        base = "SELECT * FROM events WHERE created_at > %s"
        params = [since]

        if event_type:
            base += " AND type = %s"
            params.append(event_type)
        if project_id:
            base += " AND project_id = %s"
            params.append(str(project_id))

        base += " ORDER BY created_at ASC"

        rows = self._fetch_all(base, params)
        return [Event(**row) for row in rows]

    # ============== AUDIT LOG ==============
    def audit_insert(
        self,
        table_name: str,
        record_id: uuid.UUID,
        new_data: dict,
        changed_by: Optional[uuid.UUID] = None
    ) -> AuditLog:
        """Registrar inserción en audit log."""
        row = self._fetch_one(
            """
            INSERT INTO audit_log (table_name, operation, record_id, old_data, new_data, changed_by)
            VALUES (%s, 'INSERT', %s, NULL, %s, %s)
            RETURNING *
            """,
            [table_name, str(record_id), new_data, str(changed_by) if changed_by else None]
        )
        return AuditLog(**row) if row else None

    def audit_update(
        self,
        table_name: str,
        record_id: uuid.UUID,
        old_data: dict,
        new_data: dict,
        changed_by: Optional[uuid.UUID] = None
    ) -> AuditLog:
        """Registrar actualización en audit log."""
        row = self._fetch_one(
            """
            INSERT INTO audit_log (table_name, operation, record_id, old_data, new_data, changed_by)
            VALUES (%s, 'UPDATE', %s, %s, %s, %s)
            RETURNING *
            """,
            [table_name, str(record_id), old_data, new_data,
             str(changed_by) if changed_by else None]
        )
        return AuditLog(**row) if row else None

    def audit_delete(
        self,
        table_name: str,
        record_id: uuid.UUID,
        old_data: dict,
        changed_by: Optional[uuid.UUID] = None
    ) -> AuditLog:
        """Registrar borrado en audit log."""
        row = self._fetch_one(
            """
            INSERT INTO audit_log (table_name, operation, record_id, old_data, new_data, changed_by)
            VALUES (%s, 'DELETE', %s, %s, NULL, %s)
            RETURNING *
            """,
            [table_name, str(record_id), old_data,
             str(changed_by) if changed_by else None]
        )
        return AuditLog(**row) if row else None