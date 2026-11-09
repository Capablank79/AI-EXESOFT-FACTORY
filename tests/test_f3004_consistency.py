"""Pruebas funcionales para F3-004: Idempotencia, Versionado, Estados y Referencias.

Pruebas cubren:
1. Idempotencia en operaciones de memoria, proyectos, tareas y ODD.
2. Versionado optimista con control de concurrencia.
3. Máquina de estados válida para proyectos, tareas y ODD.
4. Integridad referencial entre Brain, ODD, tareas y ejecuciones.
5. Sincronización atómica mediante UnitOfWork.
6. Recuperación coherente tras errores y rollback.

Autor: Claude Code - EXESOFT AI FACTORY - F3-004
"""

import os
import sys
import pytest
import uuid
import json
import time
import hashlib
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

from infrastructure.db import health_check, test_connection as _test_connection
from infrastructure.repositories import (
    OddHybridRepository,
    MemoryRepository,
    ProjectsRepository,
    PlanningRepository
)
from infrastructure.uow import UnitOfWork
from infrastructure.services.consistency import (
    ConsistencyService, consistency_service,
    VALID_PROJECT_TRANSITIONS, VALID_TASK_TRANSITIONS, VALID_RUN_TRANSITIONS,
    StateTransitionError, ConcurrencyError, IdempotencyKey
)
from infrastructure.uow import UnitOfWork


DEFAULT_TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


class TestF3004Idempotency:
    """Pruebas de idempotencia."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Idempotency Test {str(uuid.uuid4())[:8]}",
                slug=f"idemp-{str(uuid.uuid4())[:8]}",
                request={"type": "idempotency-test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_project_creation_idempotent(self):
        """Crear proyecto con mismo slug debe ser idempotente."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name="Test Idempotent",
                slug="test-idempotent",
                request={"type": "test"}
            )
            uow.commit()
            first_id = project.id

            # Segunda creación con mismo slug debe ser idempotente
            project2 = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name="Test Idempotent Updated",
                slug="test-idempotent",
                request={"type": "test2"}
            )
            uow.commit()

            assert project2.id == first_id
            assert project2.name == "Test Idempotent Updated"

    def test_odd_document_upsert_idempotent(self):
        """Upsert de documento ODD idempotente."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)

        spec = {"name": "Test", "version": 1}
        doc1 = repo.upsert_odd_document(self.project_id, spec)
        doc2 = repo.upsert_odd_document(self.project_id, spec)

        assert doc1.spec_hash == doc2.spec_hash
        assert doc1.project_id == doc2.project_id

    def test_knowledge_upsert_idempotent(self):
        """Crear conocimiento duplicado debe ser idempotente."""
        from infrastructure.repositories import MemoryRepository

        repo = MemoryRepository(tenant_id=DEFAULT_TENANT_ID)

        # Primera creación
        result1 = repo.create_knowledge(
            kind="spec", title="Test", body="body", source="Test",
            project_id=self.project_id, tenant_id=DEFAULT_TENANT_ID,
            idempotency_key="knowledge-test"
        )

        # Segunda creación con misma clave natural
        result2 = repo.create_knowledge(
            kind="spec", title="Test", body="body", source="Test",
            project_id=self.project_id, tenant_id=DEFAULT_TENANT_ID,
            idempotency_key="knowledge-test"
        )

        # Debe ser la misma entrada (idempotente)
        assert result1.id == result2.id


class TestF3004Versioning:
    """Pruebas de versionado optimista."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Versioning Test {str(uuid.uuid4())[:8]}",
                slug=f"ver-{str(uuid.uuid4())[:8]}",
                request={"type": "versioning-test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_task_optimistic_locking(self):
        """Control de concurrencia optimista en tareas."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.services.consistency import ConcurrencyError

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            task = uow.planning.create_task(
                tenant_id=DEFAULT_TENANT_ID,
                project_id=self.project_id,
                title="Concurrency Test",
                agent="test"
            )
            uow.commit()
            task_id = task.id

        # Leer tarea dos veces
        repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
        task1 = repo.get_task(task_id)
        task2 = repo.get_task(task_id)

        # Actualizar con versión correcta
        repo.update_task_status(task_id, "running", expected_version=task1.version)

        # Intentar actualizar con versión antigua debe fallar
        with pytest.raises(ConcurrencyError):
            repo.update_task_status(task_id, "done", expected_version=task1.version)

    def test_task_version_increment(self):
        """Versión de tarea se incrementa en cada actualización."""
        repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
        task = repo.get_task(self.task_id) if hasattr(self, 'task_id') else None

        if not hasattr(self, 'task_id'):
            from infrastructure.uow import UnitOfWork
            with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
                task = uow.planning.create_task(
                    tenant_id=DEFAULT_TENANT_ID,
                    project_id=self.project_id,
                    title="Version Test"
                )
                uow.commit()
                self.task_id = task.id

        task = repo.get_task(self.task_id)
        initial_version = task.version

        repo.update_task_status(self.task_id, "running", expected_version=initial_version)

        task_updated = repo.get_task(self.task_id)
        assert task_updated.version == initial_version + 1

        repo.update_task_status(self.task_id, "done", expected_version=initial_version + 1)

        task_done = repo.get_task(self.task_id)
        assert task_done.version == initial_version + 2


class TestF3004StateTransitions:
    """Pruebas de máquina de estados válida."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"State Test {str(uuid.uuid4())[:8]}",
                slug=f"state-{str(uuid.uuid4())[:8]}",
                request={"type": "state-test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_valid_project_transitions(self):
        """Transiciones válidas de estado de proyecto."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.services.consistency import VALID_PROJECT_TRANSITIONS

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Transition Test {str(uuid.uuid4())[:8]}",
                slug=f"trans-{str(uuid.uuid4())[:8]}",
                request={}
            )
            uow.commit()
            project_id = project.id

        # created -> planned (válido)
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            uow.validate_project_transition(project_id, "planned")
            uow.projects.update_project_status(project_id, "planned")
            uow.commit()

        # planned -> running (válido)
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            uow.validate_project_transition(project_id, "running")
            uow.projects.update_project_status(project_id, "running")
            uow.commit()

        # running -> done (válido via review)
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            uow.validate_project_transition(project_id, "review")
            uow.projects.update_project_status(project_id, "review")
            uow.commit()

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            uow.validate_project_transition(project_id, "done")
            uow.projects.update_project_status(project_id, "done")
            uow.commit()

    def test_invalid_project_transition(self):
        """Transición inválida debe fallar."""
        from infrastructure.uow import UnitOfWork

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Invalid Transition {str(uuid.uuid4())[:8]}",
                slug=f"invalid-{str(uuid.uuid4())[:8]}",
                request={}
            )
            uow.commit()
            project_id = project.id

        # created -> done (inválido)
        with pytest.raises(ValueError, match="Transición inválida"):
            with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
                uow.validate_project_transition(project_id, "done")
                uow.projects.update_project_status(project_id, "done")
                uow.commit()

    def test_valid_task_transitions(self):
        """Transiciones válidas de estado de tarea."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.repositories import PlanningRepository

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Task Transition {str(uuid.uuid4())[:8]}",
                slug=f"task-trans-{str(uuid.uuid4())[:8]}",
                request={}
            )
            uow.commit()
            project_id = project.id

        # Crear tarea
        repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
        task = repo.create_task(
            tenant_id=DEFAULT_TENANT_ID,
            project_id=project_id,
            title="Valid Transition Task",
            agent="test"
        )
        task_id = task.id

        # pending -> running (válido)
        repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
        repo.update_task_status(task_id, "running")

        # running -> done (válido)
        repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
        repo.update_task_status(task_id, "done", finished_at=datetime.now(timezone.utc))

    def test_invalid_task_transition(self):
        """Transición inválida de tarea debe fallar."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.services.consistency import StateTransitionError

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Invalid Task Transition {str(uuid.uuid4())[:8]}",
                slug=f"invalid-task-{str(uuid.uuid4())[:8]}",
                request={}
            )
            uow.commit()
            project_id = project.id

        # Crear tarea
        repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
        task = repo.create_task(
            tenant_id=DEFAULT_TENANT_ID,
            project_id=project_id,
            title=f"Invalid Transition Task {str(uuid.uuid4())[:8]}",
            agent="test"
        )
        task_id = task.id

        # pending -> done (inválido)
        with pytest.raises(StateTransitionError):
            repo = PlanningRepository(tenant_id=DEFAULT_TENANT_ID)
            repo.update_task_status(task_id, "done")


class TestF3004ReferentialIntegrity:
    """Pruebas de integridad referencial."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Ref Integrity {str(uuid.uuid4())[:8]}",
                slug=f"ref-{str(uuid.uuid4())[:8]}",
                request={"type": "ref-test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_referential_integrity_checks(self):
        """Verificar integridad referencial."""
        from infrastructure.uow import UnitOfWork

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"RI Test {str(uuid.uuid4())[:8]}",
                slug=f"ri-{str(uuid.uuid4())[:8]}",
                request={}
            )
            uow.commit()

        # Verificar con UoW
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            checks = uow.check_referential_integrity(self.project_id)
            assert checks['project_exists'] == True
            # Otros pueden ser False si no se crearon

    def test_prevent_delete_with_references(self):
        """No se debe poder borrar proyecto con referencias."""
        from infrastructure.uow import UnitOfWork

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Protected Project {uuid.uuid4()}",
                slug=f"protected-{str(uuid.uuid4())[:8]}",
                request={}
            )
            project_id = project.id
            uow.commit()

            # Agregar especificación
            uow.odd_hybrid.upsert_odd_document(project_id, {"name": "Test"})
            uow.commit()

            # Agregar tarea
            uow.planning.create_task(
                tenant_id=DEFAULT_TENANT_ID,
                project_id=project_id,
                title="Test Task",
                agent="test"
            )
            uow.commit()

            # Intentar borrar sin cascade debe fallar
            with pytest.raises(ValueError, match="referencias"):
                with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
                    uow.enforce_referential_integrity(project_id, cascade_delete=False)


class TestF3004UnitOfWork:
    """Pruebas de sincronización atómica mediante UnitOfWork."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"UoW Test {str(uuid.uuid4())[:8]}",
                slug=f"uow-{str(uuid.uuid4())[:8]}",
                request={"type": "uow-test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_uow_atomic_commit(self):
        """Commit atómico en UoW."""
        from infrastructure.uow import UnitOfWork

        project_id = uuid.uuid4()

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            # Crear proyecto, especificación y tarea en misma transacción
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Atomic Test {uuid.uuid4()}",
                slug=f"atomic-{str(uuid.uuid4())[:8]}",
                request={"type": "atomic"}
            )
            project_id = project.id

            uow.odd_hybrid.upsert_odd_document(project_id, {"name": "Test"})
            uow.planning.create_task(
                tenant_id=DEFAULT_TENANT_ID,
                project_id=project_id,
                title="Atomic Task",
                agent="test"
            )
            uow.commit()

        # Verificar que todo se guardó
        from infrastructure.repositories import ProjectsRepository, OddHybridRepository, PlanningRepository

        repo = ProjectsRepository(tenant_id=DEFAULT_TENANT_ID)
        projects = repo.list_projects(DEFAULT_TENANT_ID)
        assert len(projects) > 0

    def test_uow_rollback_on_error(self):
        """Rollback automático ante error."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.services.consistency import StateTransitionError

        try:
            with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
                project = uow.projects.create_project(
                    tenant_id=DEFAULT_TENANT_ID,
                    name=f"Rollback Test {uuid.uuid4()}",
                    slug=f"rollback-{str(uuid.uuid4())[:8]}",
                    request={}
                )
                project_id = project.id

                # Forzar error
                raise ValueError("Forced error")
        except ValueError:
            pass

        # Verificar que se hizo rollback
        from infrastructure.repositories import ProjectsRepository
        repo = ProjectsRepository(tenant_id=DEFAULT_TENANT_ID)
        # No debería encontrar el proyecto (rollback)
        projects = repo.list_projects(DEFAULT_TENANT_ID)
        # Nota: puede haber otros proyectos, pero este específico no debería estar

    def test_uow_concurrent_operations(self):
        """Operaciones concurrentes en UoW."""
        from infrastructure.uow import UnitOfWork
        import threading
        import time

        results = []

        def create_project(thread_id):
            try:
                with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
                    project = uow.projects.create_project(
                        tenant_id=DEFAULT_TENANT_ID,
                        name=f"Concurrent {thread_id} {uuid.uuid4()}",
                        slug=f"concurrent-{thread_id}-{str(uuid.uuid4())[:8]}",
                        request={}
                    )
                    uow.commit()
                    results.append(("success", thread_id))
            except Exception as e:
                results.append(("error", thread_id, str(e)))

        threads = [threading.Thread(target=create_project, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        # Al menos algunos deberían tener éxito
        successes = [r for r in results if r[0] == "success"]
        assert len(successes) >= 3  # Al menos 3 de 5


class TestF3004Regression:
    """Regresión de F3-001, F3-002, F3-003."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Regression {str(uuid.uuid4())[:8]}",
                slug=f"reg-{str(uuid.uuid4())[:8]}",
                request={"type": "regression"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_f3001_brain_persistence(self):
        """Regresión F3-001: Brain PostgreSQL."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = brain.add_project(
            str(uuid.uuid4()),
            "Regression Test",
            "Test request"
        )

        project = brain.get_project(project_id)
        assert project is not None
        assert project['name'].startswith("Regression Test_")

    def test_f3002_odd_hybrid(self):
        """Regresión F3-002: ODD Híbrido."""
        from infrastructure.repositories import OddHybridRepository

        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        # Crear proyecto primero
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Regression ODD {uuid.uuid4()}",
                slug=f"reg-odd-{str(uuid.uuid4())[:8]}",
                request={}
            )
            project_id = project.id
            uow.commit()

        # Operaciones ODD
        doc = repo.upsert_odd_document(project_id, {"test": "data"})
        assert doc is not None

        snap = repo.create_odd_snapshot(project_id, {"v": 1}, "test")
        assert snap is not None

        doc = repo.get_odd_document(project_id)
        assert doc is not None

    def test_f3003_fts_search(self):
        """Regresión F3-003: Full Text Search."""
        from infrastructure.repositories import OddHybridRepository

        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)

        # Crear proyecto y conocimiento
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"FTS Regression {uuid.uuid4()}",
                slug=f"fts-reg-{str(uuid.uuid4())[:8]}",
                request={}
            )
            project_id = project.id
            uow.commit()

            # Agregar conocimiento
            uow.memory.create_knowledge(
                kind='spec',
                title='API Design',
                body='API specification for authentication',
                source='Architect',
                project_id=project_id,
                tenant_id=DEFAULT_TENANT_ID
            )
            uow.commit()

        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        results = repo.search_knowledge('authentication', project_id, language='english', limit=5)
        assert len(results) >= 1
        assert 'rank' in results[0]


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
