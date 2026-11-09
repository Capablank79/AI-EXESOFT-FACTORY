"""Repositorio base para EXESOFT AI FACTORY.

Patron Repository con SQL parametrizado usando psycopg 3.
Proveedor de conexiones a través del pool existente o UoW compartido.

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
from typing import Optional, List, Dict, Any, Union
from contextlib import contextmanager
from dataclasses import dataclass
import psycopg
from infrastructure.db import get_connection, DatabaseError, DBConfig


class RepositoryError(DatabaseError):
    """Error general de repositorio."""
    pass


class RecordNotFoundError(RepositoryError):
    """Registro no encontrado."""
    pass


class ConnectionLease:
    """
    Wrapper que administra el ciclo de vida de la conexión.
    Cuando se usa con UoW, la conexión se comparte.
    Cuando se usa sin UoW, se toma del pool.
    """

    def __init__(self, external_conn: Optional[psycopg.Connection] = None):
        self.external_conn = external_conn
        self.managed_conn = None
        self._connection_context = None
        self._is_managed = external_conn is None

    def __enter__(self) -> psycopg.Connection:
        if self.external_conn:
            return self.external_conn
        self._connection_context = get_connection()
        self.managed_conn = self._connection_context.__enter__()
        return self.managed_conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        # No cerrar conexión si viene del UoW
        if self._connection_context and self._is_managed:
            try:
                self._connection_context.__exit__(exc_type, exc_val, exc_tb)
            finally:
                self._connection_context = None
                self.managed_conn = None


class Repository:
    """
    Base clase para repositorios con tenant isolation.

    Soporta inyección de conexión compartida para Unit of Work.
    Mantiene compatibilidad con uso aislado (pool de conexiones).
    """

    def __init__(
        self,
        tenant_id: Optional[uuid.UUID] = None,
        _conn: Optional[psycopg.Connection] = None,
        _commit_on_exit: bool = False
    ):
        """
        Initialize repository.

        Args:
            tenant_id: Optional tenant isolation
            _conn: Optional shared connection (for UoW)
            _commit_on_exit: If True, commit is handled externally
        """
        self.tenant_id = tenant_id
        self._external_conn = _conn
        self._commit_on_exit = _commit_on_exit

    def _get_connection(self) -> ConnectionLease:
        """Get connection lease wrapper."""
        return ConnectionLease(self._external_conn)

    def _apply_tenant_filter(self, query: str, params: list, table_name: str = "tasks") -> tuple:
        """Apply tenant filter to query if tenant_id is set."""
        if self.tenant_id is not None:
            # Solo aplicar si la tabla tiene tenant_id (truco: omitir si no tiene)
            tables_without_tenant = ["odd_documents", "odd_snapshots", "project_specs", "project_snapshots", "projects", "knowledge"]
            if table_name in tables_without_tenant:
                return query, params

            # No aplicar filtro en INSERT, solo en SELECT/UPDATE/DELETE
            query_upper = query.strip().upper()
            if query_upper.startswith('INSERT'):
                return query, params

            if "WHERE" in query:
                query = query.replace("WHERE", f"WHERE tenant_id = %s AND ", 1)
                params.insert(0, str(self.tenant_id))
            else:
                query += f" WHERE tenant_id = %s"
                params.insert(0, str(self.tenant_id))
        return query, params

    def _fetch_one(
        self,
        query: str,
        params: list = None,
        conn: Optional[psycopg.Connection] = None,
        table_name: str = "tasks"
    ) -> Optional[Dict[str, Any]]:
        """Execute query and fetch one row as dict.

        Converts dict params to Jsonb for JSONB columns.
        """
        if params is None:
            params = []
        query, params = self._apply_tenant_filter(query, params, table_name)

        # Convert dicts to Jsonb for JSONB columns
        from psycopg.types.json import Jsonb
        processed_params = []
        for p in params:
            if isinstance(p, dict):
                processed_params.append(Jsonb(p))
            else:
                processed_params.append(p)

        use_conn = conn or self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(query, processed_params)
                return cur.fetchone()
        else:
            with get_connection() as c:
                with c.cursor(row_factory=psycopg.rows.dict_row) as cur:
                    cur.execute(query, processed_params)
                    return cur.fetchone()

    def _fetch_all(
        self,
        query: str,
        params: list = None,
        conn: Optional[psycopg.Connection] = None,
        table_name: str = "tasks"
    ) -> List[Dict[str, Any]]:
        """Execute query and fetch all rows as dicts.

        Converts dict params to Jsonb for JSONB columns.
        """
        if params is None:
            params = []
        query, params = self._apply_tenant_filter(query, params, table_name)

        # Convert dicts to Jsonb for JSONB columns
        from psycopg.types.json import Jsonb
        processed_params = []
        for p in params:
            if isinstance(p, dict):
                processed_params.append(Jsonb(p))
            else:
                processed_params.append(p)

        use_conn = conn or self._external_conn
        if use_conn:
            with use_conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(query, processed_params)
                return cur.fetchall()
        else:
            with get_connection() as c:
                with c.cursor(row_factory=psycopg.rows.dict_row) as cur:
                    cur.execute(query, processed_params)
                    return cur.fetchall()

    def _execute(
        self,
        query: str,
        params: list = None,
        conn: Optional[psycopg.Connection] = None,
        table_name: str = "tasks"
    ) -> int:
        """Execute query (INSERT/UPDATE/DELETE) and return affected rows.

        Converts dict params to Jsonb for JSONB columns.
        """
        if params is None:
            params = []

        # Convert dicts to Jsonb for JSONB columns
        from psycopg.types.json import Jsonb
        processed_params = []
        for p in params:
            if isinstance(p, dict):
                processed_params.append(Jsonb(p))
            else:
                processed_params.append(p)

        use_conn = conn or self._external_conn
        if use_conn:
            with use_conn.cursor() as cur:
                cur.execute(query, processed_params)
                return cur.rowcount
        else:
            with get_connection() as c:
                with c.cursor() as cur:
                    cur.execute(query, processed_params)
                    return cur.rowcount


@dataclass
class PaginatedResult:
    """Resultado paginado de consulta."""
    items: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int

    @property
    def has_next(self) -> bool:
        return len(self.items) >= self.page_size and self.total > (self.page * self.page_size)

    @property
    def has_prev(self) -> bool:
        return self.page > 1
