"""Exesoft Brain PostgreSQL - Migración de persistencia.

Versión que usa PostgreSQL en lugar de SQLite.
- Memoria de trabajo, episódica y semántica
- Filtros por tenant_id, proyecto, tipo y contexto
- Aislamiento, transacciones e idempotencia

Autor: Claude Code - EXESOFT AI FACTORY - F3-001
"""

import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import json

import psycopg
from psycopg import rows
from infrastructure.db import get_connection, get_pool, DBConfig
from infrastructure.repositories.memory import MemoryRepository, Knowledge, OddDocument, OddSnapshot
from infrastructure.uow import UnitOfWork


def utc_now() -> str:
    """Retorna timestamp UTC en formato ISO."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BrainProject:
    """Proyecto en el Brain."""
    id: str
    name: str
    request: str
    status: str
    created_at: str
    updated_at: str


class PostgresBrain:
    """
    Brain sobre PostgreSQL usando MemoryRepository.

    Proporciona la misma interfaz que el Brain SQLite original
    pero persiste en PostgreSQL para escalabilidad multiusuario.
    """

    def __init__(self, db_path: Optional[str] = None, tenant_id: Optional[uuid.UUID] = None):
        """
        Inicializar Brain sobre PostgreSQL.

        Args:
            db_path: Ignorado (mantenido para compatibilidad con SQLite)
            tenant_id: Tenant para aislamiento multiusuario
        """
        self.tenant_id = tenant_id
        self._repo = MemoryRepository(tenant_id=tenant_id)
        self._legacy_db_path = db_path  # Mantener referencia para compatibilidad
        # No inicializar SQLite - solo usar PostgreSQL

    def _to_uuid(self, id_str: str) -> uuid.UUID:
        """Convertir ID string a UUID válido para PostgreSQL."""
        normalized = id_str.replace('-', '').strip()
        if len(normalized) == 32:  # UUID estándar, con o sin guiones
            return uuid.UUID(hex=normalized)
        elif len(normalized) == 12:  # UUID legado truncado - expandir a 32
            return uuid.UUID(hex=normalized + '0' * (32 - len(normalized)))
        else:
            # Generar UUID determinístico a partir del string
            return uuid.uuid5(uuid.NAMESPACE_DNS, id_str)

    @property
    def repo(self) -> MemoryRepository:
        """Obtener repositorio de memoria."""
        return self._repo

    # ============== PROJECTS ==============
    # Los proyectos se manejan a través de ProjectsRepository

    def add_project(self, project_id: str, name: str, request: str) -> str:
        """Agregar proyecto al Brain (delegado a projects repo)."""
        from infrastructure.repositories import ProjectsRepository
        proj_repo = ProjectsRepository(tenant_id=self.tenant_id)
        # Convertir project_id a UUID válido si es necesario
        pg_project_id = self._to_uuid(project_id)
        request_json = request if request.strip().startswith('{') else json.dumps({"description": request})
        # Generar slug y name únicos usando el project_id
        base_name = name
        base_slug = name.lower().replace(' ', '_').replace('\n', '').replace('\r', '')[:30]
        unique_suffix = str(pg_project_id)[:8]
        name = f"{base_name}_{unique_suffix}"
        slug = f"{base_slug}_{unique_suffix}"
        request_data = json.loads(request_json)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            project = uow.projects.create_project(
                tenant_id=self.tenant_id, project_id=pg_project_id, name=name,
                slug=slug, request=request_data
            )
            uow.memory.create_knowledge(
                kind="spec", title=f"Feature: {name}", body=request,
                source="PostgresBrain", tenant_id=self.tenant_id,
                project_id=project.id
            )
            uow.events.emit_event(
                "project.created", {"name": name}, tenant_id=self.tenant_id,
                project_id=project.id
            )
            uow.commit()
        return str(pg_project_id)

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Obtener proyecto del Brain."""
        from infrastructure.repositories import ProjectsRepository
        proj_repo = ProjectsRepository(tenant_id=self.tenant_id)
        pg_project_id = self._to_uuid(project_id)
        project = proj_repo.get_project(pg_project_id)
        return project

    def set_project_status(self, project_id: str, status: str) -> None:
        """Actualizar estado del proyecto."""
        from infrastructure.repositories import ProjectsRepository
        allowed = {'created', 'planned', 'running', 'blocked', 'review', 'done', 'cancelled'}
        if status not in allowed:
            raise ValueError('Estado inválido')

        pg_project_id = self._to_uuid(project_id)
        proj_repo = ProjectsRepository(tenant_id=self.tenant_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            if not uow.projects.update_project_status(pg_project_id, status):
                raise KeyError(f"Proyecto {project_id} no encontrado")
            uow.events.emit_event(
                "project.status", {"status": status}, tenant_id=self.tenant_id,
                project_id=pg_project_id
            )
            uow.commit()

    def tasks(self, project_id: str) -> List[Dict[str, Any]]:
        """Listar tareas de un proyecto."""
        from infrastructure.repositories import PlanningRepository
        planning_repo = PlanningRepository(tenant_id=self.tenant_id)
        pg_project_id = self._to_uuid(project_id)
        return [
            {
                **vars(task),
                "id": str(task.id),
                "tenant_id": str(task.tenant_id),
                "project_id": str(task.project_id),
                "depends_on": json.dumps(task.depends_on or []),
            }
            for task in planning_repo.list_tasks_by_project(pg_project_id)
        ]

    @staticmethod
    def _canonical_json(value) -> str:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, default=str)

    def planning_preflight(
        self, project_id: str, expected_request, *, replan_authorized_hash=None,
    ) -> Optional[Dict[str, Any]]:
        """Read-only idempotency and explicit-replan check before model routing."""
        from engine.odd import FeatureDocument
        from infrastructure.plan_approval import plan_fingerprint

        tenant_id = self._to_uuid(str(self.tenant_id))
        pg_project_id = self._to_uuid(str(project_id))
        request_hash = hashlib.sha256(
            self._canonical_json(expected_request).encode('utf-8')
        ).hexdigest()
        with UnitOfWork(tenant_id=tenant_id) as uow:
            conn = uow._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT request, status FROM projects WHERE id = %s AND tenant_id = %s",
                    (str(pg_project_id), str(tenant_id)),
                )
                project = cur.fetchone()
            if project is None:
                raise KeyError('Project not found in this tenant')
            stored_request, status = project
            if self._canonical_json(stored_request) != self._canonical_json(expected_request):
                raise ValueError('Project requirements changed; reload before planning')
            if status not in {'created', 'planned'}:
                raise ValueError('Plans can only be generated for created or planned projects')

            spec = uow.projects.get_project_spec(pg_project_id)
            metadata = spec.spec if spec and isinstance(spec.spec, dict) else {}
            document = uow.memory.get_odd_document(pg_project_id)
            has_odd_plan = False
            if document and document.body:
                try:
                    feature = FeatureDocument.from_json(document.body)
                    has_odd_plan = bool(feature.specs or feature.tasks)
                except (TypeError, ValueError, KeyError):
                    has_odd_plan = True
            tasks = uow.planning.list_tasks_by_project(pg_project_id)
            has_plan = bool(metadata or has_odd_plan or tasks)
            if not has_plan:
                if replan_authorized_hash:
                    raise ValueError('Replan authorization supplied for a project without an existing plan')
                return None

            if (not replan_authorized_hash
                    and metadata.get('planner') == 'requirements_gateway'
                    and metadata.get('request_hash') == request_hash
                    and isinstance(metadata.get('plan'), dict)):
                plan_hash = plan_fingerprint(conn, tenant_id, pg_project_id)
                uow._do_rollback()
                return {
                    'schema_version': metadata['plan'].get('schema_version'),
                    'plan_version': metadata.get('version', 1),
                    'candidate_hash': metadata.get('candidate_hash'),
                    'plan_hash': plan_hash, 'idempotent': True,
                    'specs': metadata['plan']['specs'],
                    'requirement_coverage': metadata['plan']['requirement_coverage'],
                    'tasks': self.tasks(project_id),
                }

            if not replan_authorized_hash:
                raise ValueError('Existing plan requires explicit replan authorization')
            try:
                current_hash = plan_fingerprint(conn, tenant_id, pg_project_id)
            except ValueError as exc:
                raise ValueError('Existing project state is not a complete plan') from exc
            if current_hash != replan_authorized_hash:
                raise ValueError('Replan authorization does not match the current plan fingerprint')
            if any(task.status not in {'pending', 'cancelled'} for task in tasks):
                raise ValueError('A plan cannot be replaced after task execution has started')
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM task_runs WHERE tenant_id = %s AND project_id = %s LIMIT 1",
                    (str(tenant_id), str(pg_project_id)),
                )
                if cur.fetchone():
                    raise ValueError('A plan with execution history cannot be replaced')
        return None

    def persist_validated_plan(
        self, project_id: str, plan: dict, requirements: list[str],
        expected_request, *, replan_authorized_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a validated plan through one project-locked PostgreSQL UoW.

        Model work happens before this call. The project row lock serializes
        competing planners and approval decisions; every plan record and event
        commits together or rolls back together.
        """
        from engine.odd import FeatureDocument
        from infrastructure.plan_approval import plan_fingerprint
        from infrastructure.services.consistency import ConcurrencyError

        tenant_id = self._to_uuid(str(self.tenant_id))
        pg_project_id = self._to_uuid(str(project_id))
        request_json = self._canonical_json(expected_request)
        request_hash = hashlib.sha256(request_json.encode('utf-8')).hexdigest()
        candidate_hash = hashlib.sha256(self._canonical_json({
            'tenant_id': str(tenant_id), 'project_id': str(pg_project_id),
            'request_hash': request_hash, 'plan': plan,
        }).encode('utf-8')).hexdigest()

        with UnitOfWork(tenant_id=tenant_id) as uow:
            conn = uow._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, request, status FROM projects "
                    "WHERE id = %s AND tenant_id = %s FOR UPDATE",
                    (str(pg_project_id), str(tenant_id)),
                )
                project = cur.fetchone()
            if project is None:
                raise KeyError('Project not found in this tenant')
            name, stored_request, project_status = project
            if self._canonical_json(stored_request) != request_json:
                raise ConcurrencyError('Project requirements changed while planning; regenerate the plan')
            if project_status not in {'created', 'planned'}:
                raise ValueError('Plans can only be persisted for created or planned projects')

            current_spec = uow.projects.get_project_spec(pg_project_id)
            current_metadata = current_spec.spec if current_spec and isinstance(current_spec.spec, dict) else {}
            current_document = uow.memory.get_odd_document(pg_project_id)
            current_body = current_document.body if current_document else None
            current_document_has_plan = False
            if current_body:
                try:
                    parsed_current = FeatureDocument.from_json(current_body)
                    current_document_has_plan = bool(parsed_current.specs or parsed_current.tasks)
                except (TypeError, ValueError, KeyError):
                    current_document_has_plan = True
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(
                    "SELECT id, title, description, agent, status, depends_on, attempts, "
                    "max_attempts, version FROM tasks WHERE tenant_id = %s AND project_id = %s "
                    "ORDER BY created_at, id",
                    (str(tenant_id), str(pg_project_id)),
                )
                current_tasks = cur.fetchall()

            has_plan = bool(current_tasks or current_metadata or current_document_has_plan)
            if current_metadata.get('candidate_hash') == candidate_hash:
                current_plan_hash = plan_fingerprint(conn, tenant_id, pg_project_id)
                uow._do_rollback()
                return {
                    'schema_version': plan.get('schema_version'),
                    'plan_version': current_metadata.get('version', 1),
                    'candidate_hash': candidate_hash,
                    'plan_hash': current_plan_hash,
                    'idempotent': True,
                    'specs': plan['specs'],
                    'requirement_coverage': plan['requirement_coverage'],
                    'tasks': self.tasks(project_id),
                }

            if has_plan:
                if not replan_authorized_hash:
                    raise ValueError('Existing plan requires explicit replan authorization')
                try:
                    current_plan_hash = plan_fingerprint(conn, tenant_id, pg_project_id)
                except ValueError as exc:
                    raise ValueError('Existing project state is not a complete plan') from exc
                if current_plan_hash != replan_authorized_hash:
                    raise ValueError('Replan authorization does not match the current plan fingerprint')
                if any(task['status'] not in {'pending', 'cancelled'} for task in current_tasks):
                    raise ValueError('A plan cannot be replaced after task execution has started')
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM task_runs WHERE tenant_id = %s AND project_id = %s LIMIT 1",
                        (str(tenant_id), str(pg_project_id)),
                    )
                    if cur.fetchone():
                        raise ValueError('A plan with execution history cannot be replaced')
            elif replan_authorized_hash:
                raise ValueError('Replan authorization supplied for a project without an existing plan')

            version = int(current_metadata.get('version', 1 if has_plan else 0)) + 1
            if has_plan:
                snapshot_tasks = [{
                    'id': str(task['id']), 'title': task['title'],
                    'description': task['description'], 'agent': task['agent'],
                    'status': task['status'],
                    'depends_on': [str(item) for item in (task['depends_on'] or [])],
                    'attempts': task['attempts'], 'max_attempts': task['max_attempts'],
                    'version': task['version'],
                } for task in current_tasks]
                uow.odd_hybrid.create_project_snapshot(
                    pg_project_id,
                    {
                        'plan_version': current_metadata.get('version'),
                        'plan_spec': current_metadata,
                        'odd_document': current_body,
                        'tasks': snapshot_tasks,
                        'plan_hash': current_plan_hash,
                    },
                    reason=f'plan version {current_metadata.get("version", "legacy")} superseded by version {version}',
                )
                if current_body:
                    uow.memory.create_knowledge(
                        kind='spec', title=f'Feature: {name}', body=current_body,
                        source='ODDEngine', tenant_id=tenant_id,
                        project_id=pg_project_id,
                        idempotency_key=f'planner-archive-v{version}-{current_plan_hash}',
                    )
                uow.planning.cancel_pending_project_tasks(pg_project_id)

            feature = FeatureDocument(
                project_id=str(pg_project_id), name=name,
                request=request_json,
            )
            spec_ids = {}
            for spec in plan['specs']:
                saved_spec = feature.add_spec(
                    spec['title'], spec['description'], list(spec['acceptance']),
                )
                spec_ids[spec['id']] = saved_spec.id
            task_ids = {
                task['id']: str(uuid.uuid5(pg_project_id, f'{candidate_hash}:{task["id"]}'))
                for task in plan['tasks']
            }
            odd_task_ids = {}
            for task in plan['tasks']:
                odd_task = feature.add_task(
                    spec_ids[task['spec_id']], task['agent'], task['description'],
                )
                odd_task.id = f'v{version:03d}-{task["id"]}'
                odd_task_ids[task['id']] = odd_task.id
                dependencies = [uuid.UUID(task_ids[item]) for item in task['depends_on']]
                uow.planning.create_task(
                    tenant_id=tenant_id, project_id=pg_project_id,
                    task_id=uuid.UUID(task_ids[task['id']]), title=task['title'],
                    description=task['description'], agent=task['agent'],
                    depends_on=dependencies, idempotency_key=f'planner:{candidate_hash}:{task["id"]}',
                )
                uow.odd_hybrid.link_odd_task(
                    pg_project_id, odd_task.id, task_ids[task['id']],
                )
                uow.events.emit_event(
                    'task.created',
                    {'title': task['title'], 'agent': task['agent'],
                     'plan_version': version, 'plan_candidate_hash': candidate_hash},
                    tenant_id=tenant_id, project_id=pg_project_id,
                    task_id=uuid.UUID(task_ids[task['id']]),
                )

            feature.log_event(
                'plan_revised' if has_plan else 'plan_created',
                f'Persisted requirements plan version {version} ({candidate_hash})',
            )
            feature_body = feature.to_json()
            uow.memory.upsert_odd_document(pg_project_id, feature_body)
            uow.memory.create_knowledge(
                kind='spec', title=f'Feature: {name}', body=feature_body,
                source='ODDEngine', tenant_id=tenant_id,
                project_id=pg_project_id,
                idempotency_key=f'planner-odd-v{version}-{candidate_hash}',
            )
            plan_record = {
                'planner': 'requirements_gateway', 'version': version,
                'request_hash': request_hash, 'candidate_hash': candidate_hash,
                'plan': plan, 'odd_task_ids': odd_task_ids,
            }
            uow.projects.save_project_spec(pg_project_id, plan_record)
            uow.projects.update_project_status(pg_project_id, 'planned')
            uow.events.emit_event(
                'project.status',
                {'status': 'planned', 'plan_event': 'revised' if has_plan else 'created',
                 'plan_version': version, 'plan_candidate_hash': candidate_hash},
                tenant_id=tenant_id, project_id=pg_project_id,
            )
            plan_hash = plan_fingerprint(conn, tenant_id, pg_project_id)
            uow.commit()

        return {
            'schema_version': plan.get('schema_version'),
            'plan_version': version, 'candidate_hash': candidate_hash,
            'plan_hash': plan_hash, 'idempotent': False,
            'specs': plan['specs'], 'requirement_coverage': plan['requirement_coverage'],
            'tasks': self.tasks(project_id),
        }

    def add_task(self, task_id: str, project_id: str, title: str, agent: str,
                 depends_on: tuple = (), max_attempts: int = 3,
                 description: Optional[str] = None) -> None:
        """Agregar tarea al Brain."""
        from infrastructure.services.consistency import ConcurrencyError

        if max_attempts < 1:
            raise ValueError('max_attempts debe ser positivo')

        pg_task_id = self._to_uuid(task_id)
        pg_project_id = self._to_uuid(project_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            task = uow.planning.create_task(
                tenant_id=self.tenant_id, project_id=pg_project_id, task_id=pg_task_id,
                title=title, description=description, agent=agent,
                depends_on=[self._to_uuid(item) for item in depends_on], max_attempts=max_attempts
            )
            uow.events.emit_event(
                "task.created", {"title": title, "agent": agent},
                tenant_id=self.tenant_id, project_id=pg_project_id, task_id=task.id
            )
            uow.commit()

    def update_task(self, task_id: str, status: str, increment_attempts: bool = False) -> None:
        """Actualizar estado de tarea."""
        if status not in {'pending', 'running', 'blocked', 'failed', 'done'}:
            raise ValueError('Estado inválido')

        pg_task_id = self._to_uuid(task_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            task = uow.planning.get_task(pg_task_id)
            if not task:
                raise KeyError(f"Tarea {task_id} no encontrada")
            uow.planning.update_task_status(pg_task_id, status, expected_version=task.version)
            if increment_attempts:
                uow.planning.increment_attempts(pg_task_id, expected_version=task.version + 1)
            uow.commit()

    # ============== KNOWLEDGE (Memoria) ==============

    def remember(self, kind: str, title: str, body: str, source: str,
                 project_id: Optional[str] = None) -> uuid.UUID:
        """Recordar conocimiento."""
        if not source.strip():
            raise ValueError('Se requiere fuente de conocimiento')

        pg_project_id = self._to_uuid(project_id) if project_id else None
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            knowledge = uow.memory.create_knowledge(
                kind=kind, title=title, body=body, source=source,
                tenant_id=self.tenant_id, project_id=pg_project_id,
            )
            uow.commit()
        return knowledge.id if knowledge else uuid.uuid4()

    def recall(self, query: str, project_id: Optional[str] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """Buscar conocimiento por texto."""
        if not query.strip():
            return []

        pg_project_id = self._to_uuid(project_id) if project_id else None
        results = self._repo.search_knowledge(
            query=query,
            project_id=pg_project_id,
            limit=limit
        )
        return [vars(k) for k in results]

    def list_knowledge(self, project_id: Optional[str] = None, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Listar conocimiento."""
        pg_project_id = self._to_uuid(project_id) if project_id else None
        results = self._repo.list_by_project(
            pg_project_id,
            kind=kind
        )
        return [vars(k) for k in results]

    def agent_handoffs(self, project_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Load durable agent handoffs for the tenant-scoped project timeline."""
        rows = self._repo.list_agent_handoffs(self._to_uuid(project_id), limit=limit)
        return [json.loads(row.body) for row in rows]

    # ============== ODD DOCUMENTS ==============

    def get_odd_document(self, project_id: str) -> Optional[str]:
        """Obtener documento ODD vigente."""
        pg_project_id = self._to_uuid(project_id)
        doc = self._repo.get_odd_document(pg_project_id)
        return doc.body if doc else None

    def save_odd_document(self, project_id: str, body: str) -> None:
        """Guardar documento ODD (upsert)."""
        pg_project_id = self._to_uuid(project_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            uow.memory.upsert_odd_document(pg_project_id, body)
            uow.commit()

    def persist_odd_document(self, project_id: str, title: str, body: str) -> bool:
        """
        Atomicamente actualizar el documento ODD y crear snapshot histórico.

        Retorna True si hubo cambios, False si el contenido era idéntico.
        """
        pg_project_id = self._to_uuid(project_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            current = uow.memory.get_odd_document(pg_project_id)
            if current and current.body == body:
                return False
            uow.memory.upsert_odd_document(pg_project_id, body)
            uow.memory.create_knowledge(
                kind="spec", title=title, body=body, source="ODDEngine",
                tenant_id=self.tenant_id, project_id=pg_project_id
            )
            uow.commit()
        return True

    def odd_snapshots(self, project_id: str) -> List[Dict[str, Any]]:
        """Obtener snapshots históricos de ODD."""
        pg_project_id = self._to_uuid(project_id)
        rows = self._repo._fetch_all(
            "SELECT id, body, created_at FROM knowledge "
            "WHERE project_id = %s AND kind = 'spec' AND source = 'ODDEngine' "
            "ORDER BY created_at, id",
            [str(pg_project_id)], table_name="knowledge"
        )
        return [{"id": str(row["id"]), "body": row["body"],
                 "created_at": row["created_at"].isoformat()} for row in rows]

    def _get_all_odd_snapshots(self, project_id: str) -> List[Dict[str, Any]]:
        """Obtener todos los snapshots históricos de ODD."""
        pg_project_id = self._to_uuid(project_id)
        rows = self._repo._fetch_all(
            "SELECT id, body, created_at FROM knowledge "
            "WHERE project_id = %s AND kind = 'spec' AND source = 'ODDEngine' ORDER BY created_at, id",
            [str(pg_project_id)], table_name="knowledge"
        )
        return [{"id": str(r['id']), "body": r['body'], "created_at": r['created_at'].isoformat() if hasattr(r['created_at'], 'isoformat') else r['created_at']}
                for r in rows]

    # ============== TASK LINKS ==============

    def link_odd_task(self, project_id: str, odd_task_id: str, brain_task_id: str) -> None:
        """Vincular tarea ODD con tarea Brain.

        Usa la tabla knowledge con kind='note' para almacenar los enlaces,
        ya que la tabla odd_task_links no existe en PostgreSQL y no se pueden modificar
        migraciones sin autorización. El tipo se almacena en el body JSON.
        """
        pg_project_id = self._to_uuid(project_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            uow.odd_hybrid.link_odd_task(pg_project_id, odd_task_id, brain_task_id)
            uow.commit()

    def brain_task_for_odd(self, project_id: str, odd_task_id: str) -> Optional[str]:
        """Obtener ID de tarea Brain para tarea ODD."""
        pg_project_id = self._to_uuid(project_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            return uow.odd_hybrid.get_brain_task_for_odd(pg_project_id, odd_task_id)

    def odd_task_for_brain(self, project_id: str, brain_task_id: str) -> Optional[str]:
        """Obtener ID de tarea ODD para tarea Brain."""
        pg_project_id = self._to_uuid(project_id)
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            return uow.odd_hybrid.get_odd_task_for_brain(pg_project_id, brain_task_id)

    # ============== UTILITY ==============

    def connect(self) -> 'PostgresBrainConnection':
        """Obtener context manager de conexión (compatibilidad con SQLite)."""
        return PostgresBrainConnection(self)


class PostgresBrainConnection:
    """Context manager para compatibilidad con brain.connect()."""

    def __init__(self, brain: 'PostgresBrain'):
        self.brain = brain

    def __enter__(self):
        # Para compatibilidad con código que llama db.execute()
        return self

    def execute(self, sql: str, params: tuple = None) -> Any:
        """Ejecutar SQL directo sobre la conexión PostgreSQL."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                return cur

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


# ============== LEGACY BRIDGE ==============
# Mantener compatibilidad parcial con código existente

class Brain(PostgresBrain):
    """
    Alias de retrocompatibilidad.

    El Brain original usaba SQLite. Ahora usa PostgreSQL.
    El parámetro db_path se ignora pero se mantiene para compatibilidad.

    Uso recomendado:
        brain = Brain()  # Usa PostgreSQL
        brain = Brain()  # Sin db_path - usa PostgreSQL por defecto
    """

    def __init__(self, db_path: Optional[str] = None, tenant_id: Optional[uuid.UUID] = None):
        # Ignorar db_path - ahora usamos PostgreSQL
        super().__init__(db_path=None, tenant_id=tenant_id)


__all__ = [
    'PostgresBrain',
    'Brain',  # Legacy alias
    'utc_now',
    'BrainProject',
]
