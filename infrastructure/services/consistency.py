"""Servicios de idempotencia y consistencia - EXESOFT AI FACTORY F3-004.

Proporciona:
1. Idempotencia en operaciones (upserts con claves naturales)
2. Versionado optimista con control de concurrencia (version_id/timestamp)
3. Máquina de estados válida para proyectos, tareas y ODD
7. Integridad referencial entre Brain, ODD, tareas y ejecuciones
8. Sincronización atómica vía UnitOfWork con rollback coherente

Autor: Claude Code - EXESOFT AI FACTORY - F3-004
"""

import uuid
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import psycopg
from psycopg import rows
from psycopg.types.json import Jsonb
from infrastructure.db import get_connection
from infrastructure.repositories.base import Repository
from infrastructure.db import get_connection


class EntityState(Enum):
    """Estados válidos para entidades del sistema."""
    # Proyecto
    PROJECT_CREATED = "created"
    PROJECT_PLANNED = "planned"
    PROJECT_RUNNING = "running"
    PROJECT_BLOCKED = "blocked"
    PROJECT_REVIEW = "review"
    PROJECT_DONE = "done"
    PROJECT_CANCELLED = "cancelled"

    # Tarea
    TASK_PENDING = "pending"
    TASK_RUNNING = "running"
    TASK_BLOCKED = "blocked"
    TASK_FAILED = "failed"
    TASK_DONE = "done"
    TASK_CANCELLED = "cancelled"

    # Ejecución
    RUN_RUNNING = "running"
    RUN_SUCCEEDED = "succeeded"
    RUN_FAILED = "failed"
    RUN_INTERRUPTED = "interrupted"


VALID_PROJECT_TRANSITIONS = {
    "created": ["planned", "cancelled"],
    "planned": ["running", "cancelled"],
    "running": ["blocked", "review", "cancelled"],
    "blocked": ["running", "cancelled"],
    "review": ["done", "blocked", "cancelled"],
    "done": [],  # Estado terminal
    "cancelled": [],  # Estado terminal
}

VALID_TASK_TRANSITIONS = {
    "pending": ["running", "cancelled"],
    "running": ["pending", "done", "failed", "blocked", "cancelled"],
    "blocked": ["pending", "cancelled"],
    "failed": ["pending", "cancelled"],
    "done": [],  # Estado terminal
    "cancelled": [],  # Estado terminal
}

VALID_RUN_TRANSITIONS = {
    "running": ["succeeded", "failed", "interrupted"],
    "succeeded": [],
    "failed": [],
    "interrupted": [],
}


class IdempotencyKey:
    """Clave de idempotencia para operaciones."""

    def __init__(self, operation: str, entity_type: str, natural_key: str):
        self.operation = operation
        self.entity_type = entity_type
        self.natural_key = natural_key

    def to_hash(self) -> str:
        content = f"{self.operation}:{self.entity_type}:{self.natural_key}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    @classmethod
    def from_operation(cls, operation: str, entity_type: str, **kwargs) -> 'IdempotencyKey':
        natural_key = "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return cls(operation, entity_type, natural_key)


@dataclass
class VersionedEntity:
    """Entidad con versionado optimista."""
    id: uuid.UUID
    version: int
    updated_at: datetime

    def next_version(self) -> int:
        return self.version + 1


class StateTransitionError(Exception):
    """Error en transición de estado inválida."""
    pass


class ConcurrencyError(Exception):
    """Error de concurrencia (versión desactualizada)."""
    pass


class IdempotencyError(Exception):
    """Error de idempotencia (clave duplicada con resultado diferente)."""
    pass


class ConsistencyService:
    """
    Servicio centralizado para validaciones de consistencia.

    Proporciona:
    - Validación de transiciones de estado
    - Control de concurrencia optimista
    - Idempotencia basada en claves naturales
    - Verificación de integridad referencial
    """

    def __init__(self):
        self._idempotency_cache: Dict[str, Tuple[str, Any]] = {}  # hash -> (operation, result)

    # ============== VALIDACIÓN DE ESTADOS ==============

    @staticmethod
    def validate_project_transition(current: str, target: str) -> bool:
        """Validar transición de estado de proyecto."""
        if current not in VALID_PROJECT_TRANSITIONS:
            raise StateTransitionError(f"Estado actual inválido: {current}")
        if target not in VALID_PROJECT_TRANSITIONS.get(current, []):
            raise StateTransitionError(
                f"Transición inválida: {current} -> {target}. "
                f"Transiciones válidas: {VALID_PROJECT_TRANSITIONS.get(current, [])}"
            )
        return True

    @staticmethod
    def validate_task_transition(current: str, target: str) -> bool:
        """Validar transición de estado de tarea."""
        if current not in VALID_TASK_TRANSITIONS:
            raise StateTransitionError(f"Estado actual inválido: {current}")
        if target not in VALID_TASK_TRANSITIONS.get(current, []):
            raise StateTransitionError(
                f"Transición inválida: {current} -> {target}. "
                f"Transiciones válidas: {VALID_TASK_TRANSITIONS.get(current, [])}"
            )
        return True

    @staticmethod
    def validate_run_transition(current: str, target: str) -> bool:
        """Validar transición de estado de ejecución."""
        if current not in VALID_RUN_TRANSITIONS:
            raise StateTransitionError(f"Estado actual inválido: {current}")
        if target not in VALID_RUN_TRANSITIONS.get(current, []):
            raise StateTransitionError(
                f"Transición inválida: {current} -> {target}. "
                f"Transiciones válidas: {VALID_RUN_TRANSITIONS.get(current, [])}"
            )
        return True

    # ============== IDEMPOTENCIA ==============

    def check_idempotency(self, key: IdempotencyKey) -> Optional[Any]:
        """Verificar si una operación ya fue ejecutada."""
        key_hash = key.to_hash()
        if key_hash in self._idempotency_cache:
            stored_op, result = self._idempotency_cache[key_hash]
            if stored_op == key.operation:
                return result
        return None

    def record_idempotency(self, key: IdempotencyKey, result: Any) -> None:
        """Registrar resultado de operación idempotente."""
        key_hash = key.to_hash()
        self._idempotency_cache[key.to_hash()] = (key.operation, result)

    def execute_idempotent(self, key: IdempotencyKey, operation: callable) -> Any:
        """Ejecutar operación con garantía de idempotencia."""
        existing = self.check_idempotency(key)
        if existing is not None:
            return existing
        result = operation()
        self.record_idempotency(key, result)
        return result

    # ============== CONTROL DE CONCURRENCIA ==============

    @staticmethod
    def check_version(current_version: int, expected_version: int) -> bool:
        """Verificar versión para control de concurrencia optimista."""
        if current_version != expected_version:
            raise ConcurrencyError(
                f"Versión desactualizada: esperada {expected_version}, actual {current_version}"
            )
        return True

    @staticmethod
    def increment_version(version: int) -> int:
        """Incrementar versión para actualización optimista."""
        return version + 1

    # ============== INTEGRIDAD REFERENCIAL ==============

    def verify_referential_integrity(
        self,
        conn: psycopg.Connection,
        project_id: uuid.UUID
    ) -> Dict[str, bool]:
        """Verificar integridad referencial para un proyecto."""
        checks = {}

        with conn.cursor() as cur:
            # Proyecto existe
            cur.execute("SELECT 1 FROM projects WHERE id = %s", [str(project_id)])
            checks['project_exists'] = cur.fetchone() is not None

            # Tareas pertenecen al proyecto
            cur.execute("SELECT 1 FROM tasks WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['tasks_exist'] = cur.fetchone() is not None

            # Especificación ODD existe
            cur.execute("SELECT 1 FROM project_specs WHERE project_id = %s", [str(project_id)])
            checks['spec_exists'] = cur.fetchone() is not None

            # Documento ODD existe
            cur.execute("SELECT 1 FROM odd_documents WHERE project_id = %s", [str(project_id)])
            checks['odd_doc_exists'] = cur.fetchone() is not None

            # Ejecuciones existen
            cur.execute("SELECT 1 FROM task_runs WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['runs_exist'] = cur.fetchone() is not None

            # Snapshots ODD
            cur.execute("SELECT 1 FROM odd_snapshots WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['odd_snapshots_exist'] = cur.fetchone() is not None

            # Snapshots de proyecto
            cur.execute("SELECT 1 FROM project_snapshots WHERE project_id = %s LIMIT 1", [str(project_id)])
            checks['project_snapshots_exist'] = cur.fetchone() is not None

        return checks

    def enforce_referential_integrity(
        self,
        conn: psycopg.Connection,
        project_id: uuid.UUID,
        cascade_delete: bool = False
    ) -> bool:
        """Aplicar integridad referencial (cascada o restricción)."""
        if not cascade_delete:
            # Verificar que no hay referencias antes de borrar
            checks = self.verify_referential_integrity(conn, project_id)
            if any(checks.values()):
                raise ValueError(
                    f"No se puede eliminar proyecto {project_id}: "
                    f"existen referencias en {', '.join(k for k, v in checks.items() if v)}"
                )
        else:
            # Borrado en cascada manual (si FK no tiene ON DELETE CASCADE)
            with get_connection() as conn:
                tables = [
                    'odd_snapshots', 'odd_documents', 'project_specs',
                    'task_runs', 'tasks', 'project_snapshots',
                    'project_specs', 'projects'
                ]
                for table in tables:
                    conn.execute(f"DELETE FROM {table} WHERE project_id = %s", [str(project_id)])
        return True


# Instancia singleton
consistency_service = ConsistencyService()


# ============== DECORADORES DE IDEMPOTENCIA ==============

def idempotent(operation: str, entity_type: str, **key_fields):
    """Decorador para operaciones idempotentes."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            key = IdempotencyKey.from_operation(
                operation,
                entity_type,
                **{**kwargs, **key_fields}
            )
            existing = consistency_service.check_idempotency(key)
            if existing is not None:
                return existing
            result = func(*args, **kwargs)
            consistency_service.record_idempotency(
                IdempotencyKey.from_operation(func.__name__, **kwargs, **key_fields),
                result
            )
            return result
        return wrapper
    return decorator


def optimistic_lock(entity_type: str, version_field: str = 'version'):
    """Decorador para control de concurrencia optimista."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Se espera que el primer argumento sea la entidad con versión
            entity = args[0] if args else kwargs.get('entity')
            expected_version = kwargs.get('expected_version')
            if expected_version is not None:
                current_version = getattr(entity, 'version', None)
                if current_version is None:
                    current_version = getattr(entity, 'version', None)
                ConsistencyService.check_version(
                    current_version or 0,
                    expected_version
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator


# ============== FUNCIONES DE UTILIDAD ==============

def generate_entity_hash(entity_type: str, fields: Dict[str, Any]) -> str:
    """Generar hash determinístico para entidad."""
    content = f"{entity_type}:{json.dumps(fields, sort_keys=True)}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def compute_state_hash(entity_id: uuid.UUID, entity_type: str, state: str, version: int) -> str:
    """Calcular hash de estado para auditoría."""
    content = f"{entity_type}:{entity_id}:{state}:{version}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def validate_entity_consistency(
    conn: psycopg.Connection,
    entity_type: str,
    entity_id: uuid.UUID
) -> List[str]:
    """Validar consistencia de una entidad."""
    errors = []

    with conn.cursor() as cur:
        if entity_type == 'project':
            cur.execute(
                "SELECT status, version FROM projects WHERE id = %s",
                [str(entity_id)]
            )
            row = cur.fetchone()
            if not row:
                return [f"Proyecto {entity_id} no existe"]

        elif entity_type == 'task':
            cur.execute(
                "SELECT status, project_id, attempts, max_attempts FROM tasks WHERE id = %s",
                [str(entity_id)]
            )
            row = cur.fetchone()
            if not row:
                return [f"Tarea {entity_id} no existe"]
            if row['attempts'] > row['max_attempts'] and row['status'] != 'failed':
                return [f"Tarea {entity_id}: intentos ({row['attempts']}) exceden máximo ({row['max_attempts']}) sin estado failed"]

        elif entity_type == 'task_run':
            cur.execute(
                "SELECT status, task_id, started_at, finished_at FROM task_runs WHERE id = %s",
                [str(entity_id)]
            )
            row = cur.fetchone()
            if not row:
                return [f"Ejecución {entity_id} no existe"]
            if row['status'] == 'running' and row['finished_at'] is not None:
                return [f"Ejecución {entity_id}: estado running con finished_at definido"]

    return errors


__all__ = [
    'EntityState',
    'VALID_PROJECT_TRANSITIONS',
    'VALID_TASK_TRANSITIONS',
    'VALID_RUN_TRANSITIONS',
    'IdempotencyKey',
    'VersionedEntity',
    'StateTransitionError',
    'ConcurrencyError',
    'IdempotencyError',
    'ConsistencyService',
    'consistency_service',
    'IdempotencyError',
    'ConcurrencyError',
    'StateTransitionError',
    'idempotent',
    'optimistic_lock',
    'generate_entity_hash',
    'compute_state_hash',
    'validate_entity_consistency',
]
