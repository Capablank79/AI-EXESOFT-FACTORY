"""Repositorio de proyectos - EXESOFT AI FACTORY.

Tablas: projects, project_specs, project_snapshots

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
import hashlib
from typing import Optional, List
from dataclasses import dataclass
import psycopg
from psycopg import rows
from psycopg.types.json import Jsonb
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class Project:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    description: Optional[str] = None
    request: Optional[dict] = None
    spec_url: Optional[str] = None
    status: str = "created"
    priority: Optional[int] = None
    owner_user_id: Optional[uuid.UUID] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def __getitem__(self, key: str):
        """Support legacy callers that treated project results as mappings."""
        return getattr(self, key)


@dataclass
class ProjectSpec:
    project_id: uuid.UUID
    spec: dict
    spec_hash: str
    updated_at: Optional[str] = None


@dataclass
class ProjectSnapshot:
    id: uuid.UUID
    project_id: uuid.UUID
    snapshot: dict
    reason: Optional[str] = None
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[str] = None


class ProjectsRepository(Repository):
    """Repository para proyectos y su especificación."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== PROJECTS ==============
    def create_project(
        self,
        tenant_id: uuid.UUID,
        name: str,
        slug: str,
        description: Optional[str] = None,
        request: Optional[dict] = None,
        spec_url: Optional[str] = None,
        priority: Optional[int] = None,
        owner_user_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None
    ) -> Project:
        """Crear un nuevo proyecto (idempotente por slug único)."""
        conflict_clause = (
            "ON CONFLICT (id) DO UPDATE SET" if project_id is not None
            else "ON CONFLICT (tenant_id, slug) DO UPDATE SET"
        )
        query = f"""
        INSERT INTO projects (id, tenant_id, name, slug, description, request, spec_url, priority, owner_user_id)
        VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s, %s, %s, %s, %s, %s, %s, %s)
        {conflict_clause}
            name = EXCLUDED.name,
            slug = EXCLUDED.slug,
            description = EXCLUDED.description,
            request = EXCLUDED.request,
            spec_url = EXCLUDED.spec_url,
            priority = EXCLUDED.priority,
            updated_at = NOW()
        RETURNING *
        """
        params = [
            str(project_id) if project_id else None,
            str(tenant_id), name, slug, description,
            Jsonb(request if request is not None else {}), spec_url, priority,
            str(owner_user_id) if owner_user_id else None
        ]

        use_conn = self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=rows.dict_row) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return Project(**row)
        else:
            with self._get_connection() as conn:
                with conn.cursor(row_factory=rows.dict_row) as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                    return Project(**row)

    def get_project(self, project_id: uuid.UUID) -> Optional[Project]:
        """Obtener proyecto por ID."""
        row = self._fetch_one("SELECT * FROM projects WHERE id = %s", [str(project_id)])
        return Project(**row) if row else None

    def get_project_by_slug(self, tenant_id: uuid.UUID, slug: str) -> Optional[Project]:
        """Buscar proyecto por slug dentro de un tenant."""
        row = self._fetch_one(
            "SELECT * FROM projects WHERE tenant_id = %s AND slug = %s",
            [str(tenant_id), slug]
        )
        return Project(**row) if row else None

    def update_project_status(self, project_id: uuid.UUID, status: str) -> bool:
        """Actualizar estado del proyecto."""
        query = "UPDATE projects SET status = %s, updated_at = NOW() WHERE id = %s"
        params = [status, str(project_id)]
        if self.tenant_id is not None:
            query += " AND tenant_id = %s"
            params.append(str(self.tenant_id))
        return self._execute(
            query, params
        ) > 0

    def list_projects(
        self,
        tenant_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Project]:
        """Listar proyectos de un tenant."""
        query = "SELECT * FROM projects WHERE tenant_id = %s"
        params = [str(tenant_id)]
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(query, params)
        return [Project(**row) for row in rows]

    def get_project_counts(self, tenant_id: uuid.UUID) -> dict:
        """Obtener conteo de proyectos por estado."""
        rows = self._fetch_all(
            "SELECT status, COUNT(*) as count FROM projects WHERE tenant_id = %s GROUP BY status",
            [str(tenant_id)]
        )
        return {row['status']: row['count'] for row in rows}

    # ============== PROJECT SPECS ==============
    def save_project_spec(self, project_id: uuid.UUID, spec: dict) -> ProjectSpec:
        """Guardar especificación del proyecto."""
        # Serialize specification changes with approval and execution claims.
        with self._get_connection() as conn:
            with conn.cursor() as cur:
                lock_query = "SELECT id FROM projects WHERE id = %s"
                lock_params = [str(project_id)]
                if self.tenant_id is not None:
                    lock_query += " AND tenant_id = %s"
                    lock_params.append(str(self.tenant_id))
                cur.execute(lock_query + " FOR UPDATE", lock_params)
                if cur.fetchone() is None:
                    raise ValueError('Project not found in the specification tenant')
        spec_json = str(spec)
        spec_hash = hashlib.sha256(spec_json.encode()).hexdigest()

        query = """
        INSERT INTO project_specs (project_id, spec, spec_hash)
        VALUES (%s, %s, %s)
        ON CONFLICT (project_id) DO UPDATE SET
            spec = EXCLUDED.spec,
            spec_hash = EXCLUDED.spec_hash,
            updated_at = NOW()
        RETURNING project_id, spec, spec_hash, updated_at
        """
        params = [str(project_id), Jsonb(spec), spec_hash]

        row = self._fetch_one(query, params, table_name="project_specs")
        return ProjectSpec(**row) if row else None

    def get_project_spec(self, project_id: uuid.UUID) -> Optional[ProjectSpec]:
        """Obtener especificación del proyecto."""
        row = self._fetch_one(
            "SELECT * FROM project_specs WHERE project_id = %s",
            [str(project_id)], table_name="project_specs"
        )
        return ProjectSpec(**row) if row else None

    # ============== PROJECT SNAPSHOTS ==============
    def create_snapshot(
        self,
        project_id: uuid.UUID,
        snapshot: dict,
        reason: Optional[str] = None,
        created_by: Optional[uuid.UUID] = None
    ) -> ProjectSnapshot:
        """Crear snapshot del proyecto."""
        query = """
        INSERT INTO project_snapshots (project_id, snapshot, reason, created_by)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """
        params = [
            str(project_id),
            Jsonb(snapshot),
            reason,
            str(created_by) if created_by else None
        ]

        row = self._fetch_one(query, params)
        return ProjectSnapshot(**row) if row else None

    def list_snapshots(self, project_id: uuid.UUID, limit: int = 10) -> List[ProjectSnapshot]:
        """Listar snapshots de un proyecto."""
        rows = self._fetch_all(
            """
            SELECT * FROM project_snapshots
            WHERE project_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [str(project_id), limit]
        )
        return [ProjectSnapshot(**row) for row in rows]
