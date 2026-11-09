"""Repositorio de enlaces ODD-Task - EXESOFT AI FACTORY.

Tabla: odd_task_links

Autor: Claude Code - EXESOFT AI FACTORY - F3-001
"""

import uuid
from typing import Optional
from dataclasses import dataclass
import psycopg
from psycopg import rows
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class OddTaskLink:
    """Enlace entre tarea ODD y tarea Brain."""
    project_id: uuid.UUID
    odd_task_id: str
    brain_task_id: str


class OddTaskLinksRepository(Repository):
    """Repository para enlaces entre tareas ODD y Brain."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    def link(self, project_id: uuid.UUID, odd_task_id: str, brain_task_id: str) -> OddTaskLink:
        """Crear o actualizar enlace entre tarea ODD y Brain."""
        query = """
        INSERT INTO odd_task_links (project_id, odd_task_id, brain_task_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (project_id, odd_task_id) DO UPDATE SET
            brain_task_id = EXCLUDED.brain_task_id
        RETURNING *
        """

        row = self._fetch_one(query, [str(project_id), odd_task_id, brain_task_id])
        return OddTaskLink(**row) if row else None

    def get_brain_task_for_odd(self, project_id: uuid.UUID, odd_task_id: str) -> Optional[str]:
        """Obtener ID de tarea Brain dado ID de tarea ODD."""
        row = self._fetch_one(
            "SELECT brain_task_id FROM odd_task_links WHERE project_id = %s AND odd_task_id = %s",
            [str(project_id), odd_task_id]
        )
        return row['brain_task_id'] if row else None

    def get_odd_task_for_brain(self, project_id: uuid.UUID, brain_task_id: str) -> Optional[str]:
        """Obtener ID de tarea ODD dado ID de tarea Brain."""
        row = self._fetch_one(
            "SELECT odd_task_id FROM odd_task_links WHERE project_id = %s AND brain_task_id = %s",
            [str(project_id), brain_task_id]
        )
        return row['odd_task_id'] if row else None


__all__ = ['OddTaskLinksRepository', 'OddTaskLink']