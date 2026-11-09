"""Repositorio ODD Híbrido - EXESOFT AI FACTORY.

Integra documento ODD vigente (JSONB) con specs/tasks relacionales
y snapshots históricos inmutables.

Autor: Claude Code - EXESOFT AI FACTORY - F3-002
"""

import uuid
import hashlib
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field
import psycopg
from psycopg import rows
from psycopg.types.json import Jsonb
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository


@dataclass
class OddDocument:
    """Documento ODD vigente."""
    project_id: uuid.UUID
    spec: dict
    spec_hash: str
    updated_at: Optional[str] = None


@dataclass
class OddSnapshot:
    """Snapshot histórico inmutable del ODD."""
    id: uuid.UUID
    project_id: uuid.UUID
    doc_id: Optional[uuid.UUID]
    body: Any  # Puede ser dict o str (JSON) - se convierte en __post_init__
    reason: str
    created_by: Optional[uuid.UUID] = None
    created_at: Optional[str] = None

    def __post_init__(self):
        import json
        if isinstance(self.body, str):
            try:
                self.body = json.loads(self.body)
            except json.JSONDecodeError:
                pass  # Mantener como string si no es JSON válido


@dataclass
class OddTaskLink:
    """Enlace entre tarea ODD y tarea del Brain."""
    project_id: uuid.UUID
    odd_task_id: str
    brain_task_id: str
    status: str = "pending"


class OddHybridRepository(Repository):
    """
    Repositorio para ODD Híbrido.

    Integra:
    - Documento ODD vigente en JSONB (project_specs)
    - Specs/tasks relacionales (tasks, task_domains)
    - Snapshots históricos inmutables (project_snapshots, odd_snapshots)
    """

    def __init__(self, tenant_id: Optional[uuid.UUID] = None, _conn: Optional[psycopg.Connection] = None):
        super().__init__(tenant_id=tenant_id, _conn=_conn)

    # ============== ODD DOCUMENT (VIGENTE) ==============

    def get_odd_document(self, project_id: uuid.UUID) -> Optional[OddDocument]:
        """Obtener documento ODD vigente."""
        row = self._fetch_one(
            "SELECT * FROM project_specs WHERE project_id = %s",
            [str(project_id)],
            table_name="project_specs"
        )
        return OddDocument(**row) if row else None

    def upsert_odd_document(
        self,
        project_id: uuid.UUID,
        spec: dict,
        reason: str = "ODD update"
    ) -> OddDocument:
        """Crear o actualizar documento ODD vigente con snapshot histórico."""
        spec_json = json.dumps(spec, sort_keys=True)
        spec_hash = hashlib.sha256(spec_json.encode()).hexdigest()

        now = datetime.now().isoformat()

        # Upsert documento vigente
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

        row = self._fetch_one(query, params)
        return OddDocument(**row) if row else None

    # ============== ODD SNAPSHOTS (HISTÓRICOS) ==============

    def create_odd_snapshot(
        self,
        project_id: uuid.UUID,
        body: dict,
        reason: str,
        created_by: Optional[uuid.UUID] = None
    ) -> OddSnapshot:
        """Crear snapshot histórico inmutable del ODD."""
        # Obtener último doc_id
        latest = self._fetch_one(
            "SELECT id FROM odd_snapshots WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            [str(project_id)],
            table_name="odd_snapshots"
        )
        doc_id = latest['id'] if latest else None

        # Para el primer snapshot, usar su propio ID como doc_id (se genera al insertar)
        if doc_id is None:
            # Generar un nuevo UUID para este snapshot y usarlo como doc_id
            new_id = uuid.uuid4()
            doc_id = new_id

            query = """
            INSERT INTO odd_snapshots
                (id, project_id, doc_id, body, reason, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())
            RETURNING *
            """
            params = [
                str(new_id),
                str(project_id),
                str(new_id),
                Jsonb(body),
                reason,
                str(created_by) if created_by else None
            ]
        else:
            # Snapshot subsiguiente: doc_id apunta al anterior
            query = """
            INSERT INTO odd_snapshots
                (project_id, doc_id, body, reason, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, clock_timestamp())
            RETURNING *
            """
            params = [
                str(project_id),
                str(doc_id),
                Jsonb(body),
                reason,
                str(created_by) if created_by else None
            ]

        row = self._fetch_one(query, params, table_name="odd_snapshots")
        return OddSnapshot(**row) if row else None

    def get_odd_snapshots(self, project_id: uuid.UUID, limit: int = 10) -> List[OddSnapshot]:
        """Listar snapshots históricos del ODD."""
        rows = self._fetch_all(
            """
            SELECT * FROM odd_snapshots
            WHERE project_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [str(project_id), limit],
            table_name="odd_snapshots"
        )
        return [OddSnapshot(**row) for row in rows]

    def get_odd_version(
        self,
        project_id: uuid.UUID,
        timestamp: Optional[datetime] = None
    ) -> Optional[OddSnapshot]:
        """Obtener versión específica de ODD por timestamp."""
        if timestamp is None:
            rows = self._fetch_all(
                "SELECT * FROM odd_snapshots WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
                [str(project_id)],
                table_name="odd_snapshots"
            )
        else:
            rows = self._fetch_all(
                "SELECT * FROM odd_snapshots WHERE project_id = %s AND created_at <= %s ORDER BY created_at DESC LIMIT 1",
                [str(project_id), timestamp.isoformat()],
                table_name="odd_snapshots"
            )
        return OddSnapshot(**rows[0]) if rows else None

    # ============== ODD-TASK LINKS ==============

    def link_odd_task(
        self,
        project_id: uuid.UUID,
        odd_task_id: str,
        brain_task_id: str
    ) -> OddTaskLink:
        """Vincular tarea ODD con tarea Brain."""
        # Usar knowledge como tabla de enlaces (no hay tabla odd_task_links en PG)
        now = datetime.now().isoformat()
        link_body = json.dumps({
            'type': 'odd_task_link',
            'odd_task_id': odd_task_id,
            'brain_task_id': brain_task_id
        })

        def persist(conn):
            with conn.cursor() as cur:
                # Delete existing link for this odd_task_id
                cur.execute(
                    "DELETE FROM knowledge WHERE project_id = %s AND kind = 'note' AND title = %s",
                    (str(project_id), f"Link: {odd_task_id}")
                )
                # Insert new link
                cur.execute(
                    """INSERT INTO knowledge (project_id, tenant_id, kind, title, body, source, created_at)
                       VALUES (%s, %s, 'note', %s, %s, 'ODDEngine', %s)""",
                    (str(project_id), str(self.tenant_id) if self.tenant_id else None,
                     f"Link: {odd_task_id}", link_body, now)
                )

        if self._external_conn is not None:
            persist(self._external_conn)
        else:
            with get_connection() as conn:
                persist(conn)

        return OddTaskLink(
            project_id=project_id,
            odd_task_id=odd_task_id,
            brain_task_id=brain_task_id
        )

    def get_brain_task_for_odd(self, project_id: uuid.UUID, odd_task_id: str) -> Optional[str]:
        """Obtener ID de tarea Brain para tarea ODD."""
        row = self._fetch_one(
            "SELECT body FROM knowledge WHERE project_id = %s AND kind = 'note' AND title = %s",
            [str(project_id), f"Link: {odd_task_id}"],
            table_name="knowledge"
        )
        if row:
            link_data = json.loads(row['body'])
            return link_data.get('brain_task_id')
        return None

    def get_odd_task_for_brain(self, project_id: uuid.UUID, brain_task_id: str) -> Optional[str]:
        """Obtener ID de tarea ODD para tarea Brain."""
        # body es TEXT, no JSONB, usar ::jsonb para casting
        row = self._fetch_one(
            "SELECT (body::jsonb ->> 'odd_task_id') as odd_task_id "
            "FROM knowledge WHERE project_id = %s AND kind = 'note' AND body LIKE %s",
            [str(project_id), f'%"brain_task_id": "{brain_task_id}"%'],
            table_name="knowledge"
        )
        if row and row.get('odd_task_id'):
            return row['odd_task_id']
        return None

    # ============== SYNC PROJECT SNAPSHOTS ==============

    def create_project_snapshot(
        self,
        project_id: uuid.UUID,
        snapshot: dict,
        reason: str,
        created_by: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """Crear snapshot completo del proyecto (inmutable)."""
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
        row = self._fetch_one(query, params, table_name="project_snapshots")
        return row if row else None

    def list_project_snapshots(self, project_id: uuid.UUID, limit: int = 10) -> List[Dict[str, Any]]:
        """Listar snapshots del proyecto."""
        return self._fetch_all(
            """
            SELECT * FROM project_snapshots
            WHERE project_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [str(project_id), limit],
            table_name="project_snapshots"
        )

    # ============== FULL TEXT SEARCH (FTS) ==============

    def _build_tsquery(self, query: str, language: str = 'spanish') -> str:
        """Construir tsquery a partir de una consulta de texto."""
        tokens = [t.strip() for t in query.split() if t.strip()]
        if not tokens:
            return ''
        # Construir tsquery usando AND por defecto (todas las palabras deben aparecer)
        query_str = ' & '.join([f"'{t}':*" for t in tokens])
        return query_str

    def search_knowledge(
        self,
        query: str,
        project_id: Optional[uuid.UUID] = None,
        kind: Optional[str] = None,
        language: str = 'spanish',
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Buscar conocimiento utilizando FTS (tsvector, tsquery, ts_rank)."""
        if self.tenant_id is None or project_id is None:
            return []
        tsquery_str = self._build_tsquery(query, language)
        if not tsquery_str:
            return []

        base_query = """
            SELECT k.*,
                   ts_rank_cd(
                       to_tsvector(%s::regconfig, k.title || ' ' || COALESCE(k.body, '')),
                       to_tsquery(%s::regconfig, %s::text)
                   ) AS rank
            FROM knowledge AS k
            WHERE k.tenant_id = %s
              AND k.project_id = %s
              AND to_tsvector(%s::regconfig, k.title || ' ' || COALESCE(k.body, ''))
                  @@ to_tsquery(%s::regconfig, %s::text)
        """
        params = [language, language, tsquery_str, str(self.tenant_id), str(project_id), language, language, tsquery_str]

        if kind is not None:
            base_query += " AND k.kind = %s"
            params.append(kind)

        base_query += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(base_query, params, table_name="knowledge")
        return [dict(row) for row in rows]

    def search_knowledge_simple(
        self,
        query: str,
        project_id: Optional[uuid.UUID] = None,
        language: str = 'spanish',
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Búsqueda simple de conocimiento."""
        if self.tenant_id is None or project_id is None:
            return []
        tsquery_str = self._build_tsquery(query, language)
        if not tsquery_str:
            return []

        base_query = """
            SELECT k.*,
                   ts_rank_cd(
                       to_tsvector(%s::regconfig, k.title || ' ' || COALESCE(k.body, '')),
                       to_tsquery(%s::regconfig, %s::text)
                   ) AS rank
            FROM knowledge AS k
            WHERE k.tenant_id = %s
              AND k.project_id = %s
              AND to_tsvector(%s::regconfig, k.title || ' ' || COALESCE(k.body, ''))
                  @@ to_tsquery(%s::regconfig, %s::text)
        """
        params = [language, language, tsquery_str, str(self.tenant_id), str(project_id), language, language, tsquery_str]

        base_query += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(base_query, params, table_name="knowledge")
        return [dict(row) for row in rows]

    def search_odd_document(
        self,
        project_id: uuid.UUID,
        query: str,
        language: str = 'spanish',
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Buscar en documentos ODD vigentes (project_specs)."""
        if self.tenant_id is None or project_id is None:
            return []
        tsquery_str = self._build_tsquery(query, language)
        if not tsquery_str:
            return []

        base_query = """
            SELECT ps.project_id, p.tenant_id, ps.spec, ps.spec_hash, ps.updated_at,
                   ts_rank_cd(to_tsvector(%s::regconfig, ps.spec::text), to_tsquery(%s::regconfig, %s::text)) AS rank
            FROM project_specs AS ps
            JOIN projects AS p ON p.id = ps.project_id
            WHERE p.tenant_id = %s
              AND ps.project_id = %s
              AND to_tsvector(%s::regconfig, ps.spec::text) @@ to_tsquery(%s::regconfig, %s::text)
        """
        params = [language, language, tsquery_str, str(self.tenant_id), str(project_id), language, language, tsquery_str]

        base_query += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(base_query, params, table_name="project_specs")
        return [dict(row) for row in rows]

    def search_odd_snapshots(
        self,
        project_id: uuid.UUID,
        query: str,
        language: str = 'spanish',
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Buscar en snapshots históricos de ODD."""
        if self.tenant_id is None or project_id is None:
            return []
        tsquery_str = self._build_tsquery(query, language)
        if not tsquery_str:
            return []

        base_query = """
            SELECT s.id, s.project_id, p.tenant_id, s.doc_id, s.body, s.reason, s.created_at,
                   ts_rank_cd(to_tsvector(%s::regconfig, s.body::text), to_tsquery(%s::regconfig, %s::text)) AS rank
            FROM odd_snapshots AS s
            JOIN projects AS p ON p.id = s.project_id
            WHERE p.tenant_id = %s
              AND s.project_id = %s
              AND to_tsvector(%s::regconfig, s.body::text) @@ to_tsquery(%s::regconfig, %s::text)
        """
        params = [language, language, tsquery_str, str(self.tenant_id), str(project_id), language, language, tsquery_str]

        base_query += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        rows = self._fetch_all(base_query, params, table_name="odd_snapshots")
        return [dict(row) for row in rows]

    def search_odd_hybrid(
        self,
        query: str,
        project_id: Optional[uuid.UUID] = None,
        language: str = 'spanish',
        limit: int = 50
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Búsqueda híbrida: knowledge + project_specs + odd_snapshots."""
        pid = project_id
        return {
            'knowledge': self.search_knowledge(
                query, project_id=pid, language=language, limit=limit
            ),
            'project_specs': self.search_odd_document(pid, query, language, limit) if pid else [],
            'odd_snapshots': self.search_odd_snapshots(pid, query, language, limit) if pid else []
        }

    def test_search_relevance(self, query: str, project_id: uuid.UUID, language: str = 'spanish') -> Dict[str, Any]:
        """Probar relevancia de búsqueda y retornar estadísticas."""
        results = {}

        # Buscar en knowledge
        knowledge_results = self.search_knowledge(
            query, project_id=project_id, language=language, limit=10
        )
        knowledge_count = len(knowledge_results)

        # Buscar en project_specs
        specs_results = self.search_odd_document(project_id, query, language, limit=10)
        specs_count = len(specs_results)

        # Buscar en odd_snapshots
        snapshots_results = self.search_odd_snapshots(project_id, query, language, limit=10)
        snapshots_count = len(snapshots_results)

        # Calcular relevancia total
        total = knowledge_count + specs_count + snapshots_count

        # Top resultado general
        top_result = None
        top_rank = 0

        if knowledge_results:
            top_rank = max(top_rank, knowledge_results[0].get('rank', 0))
            top_result = {'type': 'knowledge', 'result': knowledge_results[0]}
        if specs_results:
            top_rank = max(top_rank, specs_results[0].get('rank', 0))
            if top_result is None:
                top_result = {'type': 'project_specs', 'result': specs_results[0]}
        if snapshots_results:
            top_rank = max(top_rank, snapshots_results[0].get('rank', 0))
            if top_result is None:
                top_result = {'type': 'odd_snapshots', 'result': snapshots_results[0]}

        return {
            'query': query,
            'language': language,
            'project_id': str(project_id),
            'total_results': total,
            'knowledge_results': knowledge_count,
            'project_specs_results': specs_count,
            'odd_snapshots_results': snapshots_count,
            'top_result': top_result,
            'top_rank': top_rank
        }


__all__ = [
    'OddHybridRepository',
    'OddDocument',
    'OddSnapshot',
    'OddTaskLink',
]
