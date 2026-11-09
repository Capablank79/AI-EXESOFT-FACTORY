"""Repositorio de artefactos - EXESOFT AI FACTORY.

Tablas: artifacts, artifact_storage, artifact_dependencies

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath, PureWindowsPath
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

    @staticmethod
    def _workspace_file(workspace, relative_path):
        """Resolve a regular file below a workspace; reject traversal and links."""
        root = Path(workspace).resolve(strict=True)
        raw = str(relative_path)
        posix = PurePosixPath(raw)
        windows = PureWindowsPath(raw)
        if (not raw or posix.is_absolute() or windows.is_absolute() or windows.drive
                or any(part in ('.', '..') for part in raw.replace('\\', '/').split('/'))):
            raise ValueError('Artifact path must be a safe workspace-relative path')
        candidate = root.joinpath(*raw.replace('\\', '/').split('/'))
        cursor = root
        for part in raw.replace('\\', '/').split('/'):
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError('Artifact paths cannot traverse symbolic links')
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError('Artifact is outside workspace or not a regular file')
        return root, resolved

    def register_workspace_artifact(self, workspace, relative_path, *, tenant_id,
                                    project_id, task_run_id, artifact_type='test'):
        """Persist scoped metadata for an existing execution evidence file."""
        tenant_id, project_id, task_run_id = map(uuid.UUID, map(str, (tenant_id, project_id, task_run_id)))
        if self.tenant_id is None or tenant_id != uuid.UUID(str(self.tenant_id)):
            raise PermissionError('Artifact tenant does not match repository scope')
        if artifact_type not in {'code', 'config', 'doc', 'test', 'data', 'binary', 'spec'}:
            raise ValueError('Unsupported artifact type')
        root, file_path = self._workspace_file(workspace, relative_path)
        content = file_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        evidence = {}
        if file_path.suffix.lower() == '.json':
            try:
                raw = json.loads(content)
                # Allowlisted fields only: never persist command text or captured output.
                for key in ('schema', 'kind', 'runner', 'status', 'returncode',
                            'sandy_exit_code', 'tests_run', 'output_sha256',
                            'result_sha256', 'partial_ok', 'timestamp'):
                    if key in raw and isinstance(raw[key], (str, int, float, bool, type(None))):
                        evidence[key] = raw[key]
            except (ValueError, UnicodeDecodeError):
                pass
        relative = file_path.relative_to(root).as_posix()
        # Verify task run ownership and project correlation before inserting.
        run = self._fetch_one(
            'SELECT id FROM task_runs WHERE id = %s AND tenant_id = %s AND project_id = %s',
            [str(task_run_id), str(tenant_id), str(project_id)], table_name='task_runs')
        if not run:
            raise PermissionError('Task run is not owned by this tenant and project')
        mime_type = mimetypes.guess_type(file_path.name)[0]
        row = self._fetch_one(
            '''INSERT INTO artifacts
               (tenant_id, project_id, task_run_id, name, path, type, mime_type,
                size_bytes, sha256_hash, metadata, is_verified)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
               ON CONFLICT (tenant_id, project_id, name, sha256_hash)
               DO UPDATE SET task_run_id = EXCLUDED.task_run_id, path = EXCLUDED.path,
                             size_bytes = EXCLUDED.size_bytes, metadata = EXCLUDED.metadata,
                             is_verified = TRUE
               RETURNING *''',
            [str(tenant_id), str(project_id), str(task_run_id), file_path.name,
             relative, artifact_type, mime_type, len(content), digest,
             {'source': 'sandy_evidence', **evidence}], table_name='artifacts')
        return Artifact(**row)

    def retrieve_workspace_artifact(self, artifact_id, *, project_id, workspace):
        """Return verified bytes only for a record visible in this tenant/project."""
        artifact = self.get_artifact(uuid.UUID(str(artifact_id)))
        if (artifact is None or artifact.project_id != uuid.UUID(str(project_id))
                or (self.tenant_id is not None and artifact.tenant_id != uuid.UUID(str(self.tenant_id)))):
            raise PermissionError('Artifact not found in this tenant/project')
        root, file_path = self._workspace_file(workspace, artifact.path)
        content = file_path.read_bytes()
        if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256_hash:
            raise ValueError('Artifact integrity verification failed')
        self._execute('UPDATE artifacts SET is_verified = TRUE WHERE id = %s AND tenant_id = %s AND project_id = %s',
                      [str(artifact.id), str(artifact.tenant_id), str(artifact.project_id)])
        artifact.is_verified = True
        return artifact, content

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
        if self.tenant_id is not None and tenant_id is not None:
            if uuid.UUID(str(tenant_id)) != uuid.UUID(str(self.tenant_id)):
                raise PermissionError('Artifact tenant does not match repository scope')
        if tenant_id is None and self.tenant_id is not None:
            tenant_id = self.tenant_id
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
        query = "UPDATE artifacts SET is_verified = TRUE WHERE id = %s"
        params = [str(artifact_id)]
        if self.tenant_id is not None:
            query += " AND tenant_id = %s"
            params.append(str(self.tenant_id))
        return self._execute(
            query, params
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
        if self.tenant_id is not None:
            visible = self._fetch_all(
                "SELECT id, project_id FROM artifacts WHERE id IN (%s, %s)",
                [str(parent_artifact_id), str(child_artifact_id)], table_name='artifacts')
            if (len(visible) != 2 or visible[0]['project_id'] != visible[1]['project_id']):
                raise PermissionError('Both artifacts must belong to this tenant and project')
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
                      AND (%s::uuid IS NULL OR a.tenant_id = %s)
                    """,
                    (str(artifact_id), str(self.tenant_id) if self.tenant_id else None,
                     str(self.tenant_id) if self.tenant_id else None)
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
                          AND (%s::uuid IS NULL OR a.tenant_id = %s)
                        """,
                        (str(artifact_id), str(self.tenant_id) if self.tenant_id else None,
                         str(self.tenant_id) if self.tenant_id else None)
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
                      AND (%s::uuid IS NULL OR a.tenant_id = %s)
                    """,
                    (str(artifact_id), str(self.tenant_id) if self.tenant_id else None,
                     str(self.tenant_id) if self.tenant_id else None)
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
                          AND (%s::uuid IS NULL OR a.tenant_id = %s)
                        """,
                        (str(artifact_id), str(self.tenant_id) if self.tenant_id else None,
                         str(self.tenant_id) if self.tenant_id else None)
                    )
                    return [Artifact(**row) for row in cur.fetchall()]
