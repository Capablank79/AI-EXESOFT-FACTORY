"""Unit of Work - EXESOFT AI FACTORY.

Coordina múltiples repositorios dentro de una misma transacción.
Usa una única conexión PostgreSQL para todos los repositorios.

Autor: Claude Code - EXESOFT AI FACTORY
"""

import uuid
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
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
    ArtifactsRepository,
    OddHybridRepository
)
from infrastructure.services.consistency import (
    ConsistencyService,
    consistency_service,
    EntityState,
    VALID_PROJECT_TRANSITIONS,
    VALID_TASK_TRANSITIONS,
    VALID_RUN_TRANSITIONS,
    StateTransitionError,
    ConcurrencyError,
    IdempotencyKey,
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
            uow.commit()  # Requerido para persistir cambios

    Características:
    - Una sola conexión para toda la transacción
    - Rollback automático si hay excepción
    - commit() explícito; rollback si no se confirma al salir del contexto
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
        self._odd_hybrid_repo: Optional[OddHybridRepository] = None

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

    @property
    def odd_hybrid(self) -> OddHybridRepository:
        if self._odd_hybrid_repo is None:
            self._odd_hybrid_repo = OddHybridRepository(tenant_id=self.tenant_id, _conn=self._get_connection())
        return self._odd_hybrid_repo

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
            # A UoW may commit more than one logical batch before it exits.
            # Later repository calls start a fresh transaction on the same connection.
            self._committed = False
            self._rolled_back = False
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

    @contextmanager
    def _read_connection(self):
        """Use the shared transaction when active, otherwise borrow a read-only connection."""
        if self._conn is not None:
            yield self._conn
        else:
            with get_pool().connection() as conn:
                yield conn

    @staticmethod
    def _row_value(row, key: str, position: int = 0):
        """Accept both dict_row and tuple_row cursor factories."""
        return row[key] if isinstance(row, dict) else row[position]

    # ============== CONSISTENCY & IDEMPOTENCY ==============

    def validate_project_transition(self, project_id: uuid.UUID, target_status: str) -> bool:
        """Validate project state transition before commit."""
        with self._read_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM projects WHERE id = %s", [str(project_id)])
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Project {project_id} not found")

            current_status = self._row_value(row, 'status')

            # Import here to avoid circular import
            from infrastructure.services.consistency import VALID_PROJECT_TRANSITIONS

            if target_status not in VALID_PROJECT_TRANSITIONS.get(current_status, []):
                valid = VALID_PROJECT_TRANSITIONS.get(current_status, [])
                raise ValueError(
                    f"Transición inválida: {current_status} -> {target_status}. "
                    f"Transiciones válidas: {valid}"
                )
        return True

    def validate_task_transition(self, task_id: uuid.UUID, target_status: str) -> bool:
        """Validate task state transition before commit."""
        with self._read_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM tasks WHERE id = %s", [str(task_id)])
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Task {task_id} not found")

            current_status = self._row_value(row, 'status')

            from infrastructure.services.consistency import VALID_TASK_TRANSITIONS

            if target_status not in VALID_TASK_TRANSITIONS.get(current_status, []):
                valid = VALID_TASK_TRANSITIONS.get(current_status, [])
                raise ValueError(
                    f"Invalid task transition: {current_status} -> {target_status}. "
                    f"Valid transitions: {valid}"
                )
        return True

    def validate_run_transition(self, run_id: uuid.UUID, target_status: str) -> bool:
        """Validate task run state transition before commit."""
        with self._read_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM task_runs WHERE id = %s", [str(run_id)])
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Task run {run_id} not found")

            current_status = self._row_value(row, 'status')

            from infrastructure.services.consistency import VALID_RUN_TRANSITIONS

            if target_status not in VALID_RUN_TRANSITIONS.get(current_status, []):
                valid = VALID_RUN_TRANSITIONS.get(current_status, [])
                raise ValueError(
                    f"Invalid run transition: {current_status} -> {target_status}. "
                    f"Valid transitions: {valid}"
                )
        return True

    def check_referential_integrity(self, project_id: uuid.UUID) -> Dict[str, bool]:
        """Check whether a project's related records exist (read-only)."""
        return self.verify_referential_integrity(project_id)

    def execute_idempotent(self, key: 'IdempotencyKey', operation: callable) -> Any:
        """Execute operation with idempotency guarantee."""
        from infrastructure.services.consistency import consistency_service
        return consistency_service.execute_idempotent(key, operation)

    def create_idempotency_key(self, operation: str, entity_type: str, **kwargs) -> 'IdempotencyKey':
        """Create idempotency key for operation."""
        from infrastructure.services.consistency import IdempotencyKey
        return IdempotencyKey.from_operation(operation, entity_type, **kwargs)

    def check_version(self, entity_type: str, entity_id: uuid.UUID, expected_version: int) -> bool:
        """Check optimistic lock version."""
        table_map = {
            'project': ('projects', 'version'),
            'task': ('tasks', 'version'),
            'spec': ('project_specs', 'version'),
        }

        if entity_type not in table_map:
            raise ValueError(f"Unknown entity type: {entity_type}")

        table, column = table_map[entity_type]

        id_column = 'project_id' if entity_type == 'spec' else 'id'
        with self._read_connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {column} FROM {table} WHERE {id_column} = %s", [str(entity_id)])
            row = cur.fetchone()
            if not row:
                raise ValueError(f"{entity_type} {entity_id} not found")

            current_version = self._row_value(row, column)
            if current_version != expected_version:
                from infrastructure.services.consistency import ConcurrencyError
                raise ConcurrencyError(
                    f"Version conflict: expected {expected_version}, current {current_version}"
                )
        return True

    def verify_referential_integrity(self, project_id: uuid.UUID) -> Dict[str, bool]:
        """Verify referential integrity for project."""
        checks = {}
        with self._read_connection() as conn, conn.cursor() as cur:
            # Project exists
            cur.execute("SELECT 1 FROM projects WHERE id = %s", [str(project_id)])
            checks['project_exists'] = cur.fetchone() is not None

            # Tasks exist
            cur.execute("SELECT 1 FROM tasks WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['tasks_exist'] = cur.fetchone() is not None

            # Spec exists
            cur.execute("SELECT 1 FROM project_specs WHERE project_id = %s", [str(project_id)])
            checks['spec_exists'] = cur.fetchone() is not None

            # ODD doc exists
            cur.execute("SELECT 1 FROM odd_documents WHERE project_id = %s", [str(project_id)])
            checks['odd_doc_exists'] = cur.fetchone() is not None

            # Runs exist
            cur.execute("SELECT 1 FROM task_runs WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['runs_exist'] = cur.fetchone() is not None

            # ODD snapshots
            cur.execute("SELECT 1 FROM odd_snapshots WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['odd_snapshots_exist'] = cur.fetchone() is not None

            # Project snapshots
            cur.execute("SELECT 1 FROM project_snapshots WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['project_snapshots_exist'] = cur.fetchone() is not None

            return checks

    def enforce_referential_integrity(self, project_id: uuid.UUID, cascade_delete: bool = False) -> bool:
        """Enforce referential integrity for project."""
        if self._conn is None:
            raise UnitOfWorkError("No active connection")

        from infrastructure.services.consistency import consistency_service

        if not cascade_delete:
            # Check for references before delete
            checks = self.verify_referential_integrity(project_id)
            referenced = {k: v for k, v in checks.items() if k != 'project_exists' and v}
            if referenced:
                raise ValueError(
                    f"No se puede borrar el proyecto {project_id}: "
                    f"existen referencias en {', '.join(referenced)}"
                )
        else:
            raise NotImplementedError(
                "Cascade delete requires an approved FK-aware implementation; no records deleted"
            )
        return True


__all__ = [
    'UnitOfWork',
    'UnitOfWorkError',
]
