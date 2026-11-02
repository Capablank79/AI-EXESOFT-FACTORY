"""Repositorio de memoria (Brain) - EXESOFT AI FACTORY.

Tablas: knowledge, odd_documents, odd_snapshots

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
class Knowledge:
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    kind: str
    title: str
    body: str
    source: str
    tags: Optional[List[str]] = None
    confidence: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class OddDocument:
    project_id: uuid.UUID
    body: str
    last_updated: str
    updated_by: Optional[uuid.UUID] = None


@dataclass
class OddSnapshot:
    id: uuid.UUID
    project_id: uuid.UUID
    body: str
    reason: str
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None


class MemoryRepository(Repository):
    """Repository para memoria del sistema (Brain)."""

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== KNOWLEDGE ==============
    def create_knowledge(
        self,
        kind: str,
        title: str,
        body: str,
        source: str,
        tenant_id: Optional[uuid.UUID] = None,
        project_id: Optional[uuid.UUID] = None,
        tags: Optional[List[str]] = None,
        confidence: Optional[float] = None
    ) -> Knowledge:
        """Crear entrada de conocimiento."""
        query = """
        INSERT INTO knowledge (tenant_id, project_id, kind, title, body, source, tags, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        params = [
            str(tenant_id) if tenant_id else None, str(project_id) if project_id else None,
            kind, title, body, source, tags, confidence
        ]

        row = self._fetch_one(query, params)
        return Knowledge(**row) if row else None

    def get_knowledge(self, knowledge_id: uuid.UUID) -> Optional[Knowledge]:
        """Obtener entrada de conocimiento por ID."""
        row = self._fetch_one("SELECT * FROM knowledge WHERE id = %s", [str(knowledge_id)])
        return Knowledge(**row) if row else None

    def search_knowledge(
        self,
        query: str,
        project_id: Optional[uuid.UUID] = None,
        kind: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50
    ) -> List[Knowledge]:
        """Buscar conocimiento por texto o filtros."""
        base = "SELECT * FROM knowledge WHERE to_tsvector('english', title || ' ' || COALESCE(body, '')) @@ plainto_tsquery('english', %s)"
        params = [query]

        if project_id:
            base += " AND project_id = %s"
            params.append(str(project_id))
        if kind:
            base += " AND kind = %s"
            params.append(kind)
        if tag:
            base += " AND %s = ANY(tags)"
            params.append(tag)

        base += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(base, params)
        return [Knowledge(**row) for row in rows]

    def list_by_project(self, project_id: uuid.UUID, kind: Optional[str] = None) -> List[Knowledge]:
        """Listar conocimiento de un proyecto."""
        query = "SELECT * FROM knowledge WHERE project_id = %s"
        params = [str(project_id)]
        if kind:
            query += " AND kind = %s"
            params.append(kind)
        query += " ORDER BY created_at DESC"

        rows = self._fetch_all(query, params)
        return [Knowledge(**row) for row in rows]

    # ============== ODD DOCUMENTS ==============
    def get_odd_document(self, project_id: uuid.UUID) -> Optional[OddDocument]:
        """Obtener documento ODD vigente de un proyecto."""
        row = self._fetch_one(
            "SELECT * FROM odd_documents WHERE project_id = %s",
            [str(project_id)]
        )
        return OddDocument(**row) if row else None

    def upsert_odd_document(
        self,
        project_id: uuid.UUID,
        body: str,
        updated_by: Optional[uuid.UUID] = None
    ) -> OddDocument:
        """Crear o actualizar documento ODD."""
        query = """
        INSERT INTO odd_documents (project_id, body, last_updated, updated_by)
        VALUES (%s, %s, NOW(), %s)
        ON CONFLICT (project_id) DO UPDATE SET
            body = EXCLUDED.body,
            last_updated = NOW(),
            updated_by = EXCLUDED.updated_by
        RETURNING *
        """
        params = [str(project_id), body, str(updated_by) if updated_by else None]

        row = self._fetch_one(query, params)
        return OddDocument(**row) if row else None

    def get_odd_version(
        self,
        project_id: uuid.UUID,
        timestamp: Optional[datetime] = None
    ) -> Optional[OddSnapshot]:
        """Obtener versión específica de ODD por timestamp."""
        if timestamp is None:
            rows = self._fetch_all("""
                SELECT * FROM odd_snapshots
                WHERE project_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, [str(project_id)])
        else:
            rows = self._fetch_all("""
                SELECT * FROM odd_snapshots
                WHERE project_id = %s AND created_at <= %s
                ORDER BY created_at DESC
                LIMIT 1
            """, [str(project_id), timestamp])

        return OddSnapshot(**rows[0]) if rows else None

    # ============== ODD SNAPSHOTS ==============
    def create_odd_snapshot(
        self,
        project_id: uuid.UUID,
        body: str,
        reason: str,
        created_by: Optional[uuid.UUID] = None
    ) -> OddSnapshot:
        """Crear snapshot de documento ODD."""
        # Get latest doc_id
        latest = self._fetch_one(
            "SELECT id FROM odd_snapshots WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            [str(project_id)]
        )
        doc_id = latest['id'] if latest else None

        query = """
        INSERT INTO odd_snapshots (project_id, doc_id, body, reason, created_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """
        params = [str(project_id), str(doc_id) if doc_id else None, body, reason,
                  str(created_by) if created_by else None]

        row = self._fetch_one(query, params)
        return OddSnapshot(**row) if row else None