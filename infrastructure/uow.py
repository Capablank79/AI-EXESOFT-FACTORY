"""Unit of Work - EXESOFT AI FACTORY.

Coordina múltiples repositorios dentro de una misma transacción.
Usa una única conexión PostgreSQL para todos los repositorios.

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
from typing import Optional, Dict, Any
import psycopg
import psycopg.errors
from infrastructure.db import DatabaseError, get_pool
from infrastructure.repositories import (
    IdentityRepository,
    ProjectsRepository,
    PlanningRepository,
    ExecutionRepository,
    MemoryRepository,
    EventsRepository,
    QualityRepository,
    ProvidersRepository,
    ArtifactsRepository
)


class UnitOfWorkError(DatabaseError):
    """Error en el Unit of Work."""
    pass


class UnitOfWork:
    """
    Unit of Work para coordinar transacciones entre repositorios.

    Uso con context manager:
        with UnitOfWork(tenant_id=tenant_id) as uow:
            uow.projects.create_project(...)
            uow.planning.create_task(...)
            uow.events.emit_event(...)
            uow.commit()  # Automático si el contexto termina sin excepciones

    Características:
    - Una sola conexión para toda la transacción
    - Rollback automático si hay excepción
    - commit() explícito o automático al salir del contexto
    - Aislamiento por tenant_id
    - No expone credenciales (usa pool configuración existente)
    """

    def __init__(self, tenant_id: Optional[uuid.UUID] = None):
        """
        Initialize Unit of Work.

        Args:
            tenant_id: Tenant isolation for all operations
        """
        self.tenant_id = tenant_id
        self._conn: Optional[psycopg.Connection] = None
        self._pool_connection = None
        self._committed = False
        self._rolled_back = False

        # Repositories will be lazily initialized with shared connection
        self._identity_repo: Optional[IdentityRepository] = None
        self._projects_repo: Optional[ProjectsRepository] = None
        self._planning_repo: Optional[PlanningRepository] = None
        self._execution_repo: Optional[ExecutionRepository] = None
        self._memory_repo: Optional[MemoryRepository] = None
        self._events_repo: Optional[EventsRepository] = None
        self._quality_repo: Optional[QualityRepository] = None
        self._providers_repo: Optional[ProvidersRepository] = None
        self._artifacts_repo: Optional[ArtifactsRepository] = None

    def _get_connection(self) -> psycopg.Connection:
        """Get or create shared connection."""
        if self._conn is None:
            self._pool_connection = get_pool().connection()
            try:
                self._conn = self._pool_connection.__enter__()
            except Exception:
                self._pool_connection = None
                raise
        return self._conn

    # Lazy properties for repositories
    @property
    def identity(self) -> IdentityRepository:
        if self._identity_repo is None:
            self._identity_repo = IdentityRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._identity_repo

    @property
    def projects(self) -> ProjectsRepository:
        if self._projects_repo is None:
            self._projects_repo = ProjectsRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._projects_repo

    @property
    def planning(self) -> PlanningRepository:
        if self._planning_repo is None:
            self._planning_repo = PlanningRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._planning_repo

    @property
    def execution(self) -> ExecutionRepository:
        if self._execution_repo is None:
            self._execution_repo = ExecutionRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._execution_repo

    @property
    def memory(self) -> MemoryRepository:
        if self._memory_repo is None:
            self._memory_repo = MemoryRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._memory_repo

    @property
    def events(self) -> EventsRepository:
        if self._events_repo is None:
            self._events_repo = EventsRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._events_repo

    @property
    def quality(self) -> QualityRepository:
        if self._quality_repo is None:
            self._quality_repo = QualityRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._quality_repo

    @property
    def providers(self) -> ProvidersRepository:
        if self._providers_repo is None:
            self._providers_repo = ProvidersRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._providers_repo

    @property
    def artifacts(self) -> ArtifactsRepository:
        if self._artifacts_repo is None:
            self._artifacts_repo = ArtifactsRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._artifacts_repo

    def __enter__(self) -> 'UnitOfWork':
        """Enter context, acquire connection."""
        self._get_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context, rollback if not committed and there was an exception."""
        try:
            if exc_type is not None:
                # Exception occurred - rollback
                self._do_rollback()
            elif not self._committed:
                # No explicit commit - rollback to not leave half-committed
                self._do_rollback()
        finally:
            self._close_connection()

        # Don't suppress exceptions
        return False

    def commit(self) -> None:
        """Commit the transaction."""
        if self._committed:
            return
        if self._conn is None:
            raise UnitOfWorkError("No connection available")

        try:
            self._conn.commit()
            self._committed = True
        except Exception as e:
            self._do_rollback()
            raise UnitOfWorkError(f"Commit failed: {e}") from e

    def _do_rollback(self) -> None:
        """Do the rollback."""
        if self._rolled_back or self._conn is None:
            return

        try:
            self._conn.rollback()
            self._rolled_back = True
        except Exception as e:
            if not self._conn.closed:
                self._conn.rollback()

    def _close_connection(self) -> None:
        """Return the borrowed connection to the shared pool."""
        pool_connection = self._pool_connection
        self._pool_connection = None
        self._conn = None
        if pool_connection is not None:
            pool_connection.__exit__(None, None, None)

    def is_active(self) -> bool:
        """Check if connection is still active."""
        if self._conn is None:
            return False
        try:
            self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False


__all__ = [
    'UnitOfWork',
    'UnitOfWorkError',
]
