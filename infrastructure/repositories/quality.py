"""Repositorio de calidad - EXESOFT AI FACTORY.

Tablas: quality_gates, validations, evidence

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
class QualityGate:
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    name: str
    type: str
    config: dict
    is_active: bool = True
    created_at: Optional[datetime] = None


@dataclass
class Validation:
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    task_id: Optional[uuid.UUID]
    quality_gate_id: Optional[uuid.UUID]
    status: str
    evidence: Optional[List[str]] = None
    metrics: Optional[dict] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class Evidence:
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    task_run_id: Optional[uuid.UUID]
    type: str
    hash: str
    url: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None


class QualityRepository(Repository):
    """Repository para quality gates, validaciones y evidencias."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== QUALITY GATES ==============
    def create_quality_gate(
        self,
        name: str,
        type: str,
        config: dict,
        tenant_id: Optional[uuid.UUID] = None
    ) -> QualityGate:
        """Crear una nueva quality gate."""
        row = self._fetch_one(
            """
            INSERT INTO quality_gates (tenant_id, name, type, config)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            [str(tenant_id) if tenant_id else None, name, type, config]
        )
        return QualityGate(**row) if row else None

    def get_quality_gate(self, gate_id: uuid.UUID) -> Optional[QualityGate]:
        """Obtener quality gate por ID."""
        row = self._fetch_one("SELECT * FROM quality_gates WHERE id = %s", [str(gate_id)])
        return QualityGate(**row) if row else None

    def list_quality_gates(
        self,
        tenant_id: uuid.UUID,
        active_only: bool = True
    ) -> List[QualityGate]:
        """Listar quality gates de un tenant."""
        query = "SELECT * FROM quality_gates WHERE tenant_id = %s"
        params = [str(tenant_id)]
        if active_only:
            query += " AND is_active = TRUE"
        query += " ORDER BY created_at DESC"

        rows = self._fetch_all(query, params)
        return [QualityGate(**row) for row in rows]

    # ============== VALIDATIONS ==============
    def create_validation(
        self,
        quality_gate_id: uuid.UUID,
        project_id: uuid.UUID,
        task_id: Optional[uuid.UUID] = None,
        tenant_id: Optional[uuid.UUID] = None
    ) -> Validation:
        """Crear una nueva validación."""
        row = self._fetch_one(
            """
            INSERT INTO validations (quality_gate_id, project_id, task_id, tenant_id, status)
            VALUES (%s, %s, %s, %s, 'pending')
            RETURNING *
            """,
            [str(quality_gate_id), str(project_id), str(task_id) if task_id else None,
             str(tenant_id) if tenant_id else None]
        )
        return Validation(**row) if row else None

    def get_validation(self, validation_id: uuid.UUID) -> Optional[Validation]:
        """Obtener validación por ID."""
        row = self._fetch_one("SELECT * FROM validations WHERE id = %s", [str(validation_id)])
        return Validation(**row) if row else None

    def update_validation_status(
        self,
        validation_id: uuid.UUID,
        status: str,
        evidence: Optional[List[str]] = None,
        metrics: Optional[dict] = None,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None
    ) -> Validation:
        """Actualizar estado de validación."""
        row = self._fetch_one(
            """
            UPDATE validations
            SET status = %s, evidence = %s, metrics = %s, started_at = %s, finished_at = %s
            WHERE id = %s
            RETURNING *
            """,
            [status, evidence or [], metrics,
             started_at.isoformat() if started_at else None,
             finished_at.isoformat() if finished_at else None,
             str(validation_id)]
        )
        return Validation(**row) if row else None

    def list_validations(
        self,
        project_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Validation]:
        """Listar validaciones de un proyecto."""
        query = "SELECT * FROM validations WHERE project_id = %s"
        params = [str(project_id)]
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(query, params)
        return [Validation(**row) for row in rows]

    # ============== EVIDENCE ==============
    def create_evidence(
        self,
        type: str,
        hash: str,
        tenant_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        task_run_id: Optional[uuid.UUID] = None,
        url: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> Evidence:
        """Crear evidencia verificable."""
        row = self._fetch_one(
            """
            INSERT INTO evidence (tenant_id, project_id, task_run_id, type, hash, url, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [str(tenant_id) if tenant_id else None, str(project_id) if project_id else None,
             str(task_run_id) if task_run_id else None, type, hash, url, metadata]
        )
        return Evidence(**row) if row else None

    def get_evidence(self, evidence_id: uuid.UUID) -> Optional[Evidence]:
        """Obtener evidencia por ID."""
        row = self._fetch_one("SELECT * FROM evidence WHERE id = %s", [str(evidence_id)])
        return Evidence(**row) if row else None

    def get_evidence_by_hash(self, hash: str) -> Optional[Evidence]:
        """Buscar evidencia por hash (SHA-256)."""
        row = self._fetch_one("SELECT * FROM evidence WHERE hash = %s", [hash])
        return Evidence(**row) if row else None

    def list_evidence(
        self,
        project_id: Optional[uuid.UUID] = None,
        evidence_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Evidence]:
        """Listar evidencias."""
        query = "SELECT * FROM evidence"
        params = []
        conditions = []

        if project_id:
            conditions.append("project_id = %s")
            params.append(str(project_id))
        if evidence_type:
            conditions.append("type = %s")
            params.append(evidence_type)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(query, params)
        return [Evidence(**row) for row in rows]