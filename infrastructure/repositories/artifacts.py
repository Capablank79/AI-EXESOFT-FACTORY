"""Repositorio de artefactos - EXESOFT AI FACTORY.

Tablas: artifacts, artifact_storage, artifact_dependencies

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
import hashlib
from typing import Optional, List
from datetime import datetime
from dataclasses import dataclass
import psycopg
from psycopg import rows
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class Artifact:
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    task_run_id: Optional[uuid.UUID]
    name: str
    path: str
    type: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256_hash: str = ""
    metadata: Optional[dict] = None
    is_verified: bool = False
    created_at: Optional[datetime] = None


@dataclass
class ArtifactStorage:
    artifact_id: uuid.UUID
    storage_path: str
    storage_type: str
    last_accessed: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class ArtifactDependency:
    parent_artifact_id: uuid.UUID
    child_artifact_id: uuid.UUID
    created_at: Optional[datetime] = None


class ArtifactsRepository(Repository):
    """Repository para artefactos entregables."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    def _compute_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()

    # ============== ARTIFACTS ==============
    def create_artifact(
        self,
        name: str,
        path: str,
        type: str,
        content: Optional[str] = None,
        sha256_hash: Optional[str] = None,
        mime_type: Optional[str] = None,
        tenant_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        task_run_id: Optional[uuid.UUID] = None,
        metadata: Optional[dict] = None
    ) -> Artifact:
        """Crear un artefacto."""
        # Compute hash if not provided
        if sha256_hash is None and content:
            sha256_hash = self._compute_hash(content)
        elif sha256_hash is None:
            raise ValueError("Either sha256_hash or content must be provided")

        # Compute size_bytes from content if available
        size_bytes = len(content.encode()) if content else None

        # Check if artifact already exists with same hash
        existing = self.get_artifact_by_hash(sha256_hash)
        if existing:
            return existing

        row = self._fetch_one(
            """
            INSERT INTO artifacts (tenant_id, project_id, task_run_id, name, path, type, mime_type, size_bytes, sha256_hash, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [str(tenant_id) if tenant_id else None, str(project_id) if project_id else None,
             str(task_run_id) if task_run_id else None, name, path, type, mime_type,
             size_bytes, sha256_hash, metadata]
        )
        return Artifact(**row) if row else None

    def get_artifact(self, artifact_id: uuid.UUID) -> Optional[Artifact]:
        """Obtener artefacto por ID."""
        row = self._fetch_one("SELECT * FROM artifacts WHERE id = %s", [str(artifact_id)])
        return Artifact(**row) if row else None

    def get_artifact_by_hash(self, sha256_hash: str) -> Optional[Artifact]:
        """Buscar artefacto por hash."""
        row = self._fetch_one("SELECT * FROM artifacts WHERE sha256_hash = %s", [sha256_hash])
        return Artifact(**row) if row else None

    def list_artifacts(
        self,
        project_id: Optional[uuid.UUID] = None,
        artifact_type: Optional[str] = None,
        verified_only: bool = False,
        limit: int = 100
    ) -> List[Artifact]:
        """Listar artefactos."""
        query = "SELECT * FROM artifacts"
        params = []
        conditions = []

        if project_id:
            conditions.append("project_id = %s")
            params.append(str(project_id))
        if artifact_type:
            conditions.append("type = %s")
            params.append(artifact_type)
        if verified_only:
            conditions.append("is_verified = TRUE")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(query, params)
        return [Artifact(**row) for row in rows]

    def verify_artifact(self, artifact_id: uuid.UUID) -> bool:
        """Marcar artefacto como verificado."""
        return self._execute(
            "UPDATE artifacts SET is_verified = TRUE WHERE id = %s",
            [str(artifact_id)]
        ) > 0

    # ============== ARTIFACT STORAGE ==============
    def save_storage_metadata(
        self,
        artifact_id: uuid.UUID,
        storage_path: str,
        storage_type: str
    ) -> ArtifactStorage:
        """Guardar metadata de almacenamiento."""
        row = self._fetch_one(
            """
            INSERT INTO artifact_storage (artifact_id, storage_path, storage_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (artifact_id) DO UPDATE SET
                storage_path = EXCLUDED.storage_path,
                storage_type = EXCLUDED.storage_type
            RETURNING *
            """,
            [str(artifact_id), storage_path, storage_type]
        )
        return ArtifactStorage(**row) if row else None

    # ============== ARTIFACT DEPENDENCIES ==============
    def add_dependency(
        self,
        parent_artifact_id: uuid.UUID,
        child_artifact_id: uuid.UUID
    ) -> bool:
        """Agregar dependencia entre artefactos."""
        rows = self._execute(
            """
            INSERT INTO artifact_dependencies (parent_artifact_id, child_artifact_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            [str(parent_artifact_id), str(child_artifact_id)]
        )
        return rows > 0

    def get_dependencies(self, artifact_id: uuid.UUID) -> List[Artifact]:
        """Obtener dependencias de un artefacto (child artifacts)."""
        use_conn = self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT a.* FROM artifacts a
                    JOIN artifact_dependencies ad ON a.id = ad.child_artifact_id
                    WHERE ad.parent_artifact_id = %s
                    """,
                    (str(artifact_id),)
                )
                return [Artifact(**row) for row in cur.fetchall()]
        else:
            with self._get_connection() as conn:
                with conn.cursor(row_factory=rows.dict_row) as cur:
                    cur.execute(
                        """
                        SELECT a.* FROM artifacts a
                        JOIN artifact_dependencies ad ON a.id = ad.child_artifact_id
                        WHERE ad.parent_artifact_id = %s
                        """,
                        (str(artifact_id),)
                    )
                    return [Artifact(**row) for row in cur.fetchall()]

    def get_dependents(self, artifact_id: uuid.UUID) -> List[Artifact]:
        """Obtener artefactos que dependen de este (parent artifacts)."""
        use_conn = self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=rows.dict_row) as cur:
                cur.execute(
                    """
                    SELECT a.* FROM artifacts a
                    JOIN artifact_dependencies ad ON a.id = ad.parent_artifact_id
                    WHERE ad.child_artifact_id = %s
                    """,
                    (str(artifact_id),)
                )
                return [Artifact(**row) for row in cur.fetchall()]
        else:
            with self._get_connection() as conn:
                with conn.cursor(row_factory=rows.dict_row) as cur:
                    cur.execute(
                        """
                        SELECT a.* FROM artifacts a
                        JOIN artifact_dependencies ad ON a.id = ad.parent_artifact_id
                        WHERE ad.child_artifact_id = %s
                        """,
                        (str(artifact_id),)
                    )
                    return [Artifact(**row) for row in cur.fetchall()]
