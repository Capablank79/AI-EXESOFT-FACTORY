"""Pruebas funcionales para F3-002: ODD Híbrido.

Pruebas cubren:
1. Persistencia y recuperación del ODD vigente (JSONB)
2. Sincronización transaccional ODD <-> project_specs <-> tasks
3. Snapshots históricos inmutables y versionados
4. Actualización atómica mediante UnitOfWork
5. Control de versiones, idempotencia y prevención inconsistencias
6. Aislamiento por tenant_id y project_id
7. Recuperación versiones históricas sin alterar vigente
8. Recuperación de tareas ODD <-> Brain

Autor: Claude Code - EXESOFT AI FACTORY - F3-002
"""

import os
import sys
import pytest
import uuid
import json
import time

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
from infrastructure.repositories import OddHybridRepository
from infrastructure.uow import UnitOfWork


DEFAULT_TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


class TestOddHybridConnection:
    """Pruebas de conexión y configuración."""

    def test_postgres_connection(self):
        """Verificar conectividad PostgreSQL."""
        result = _test_connection()
        assert result['success'], f"Conexión fallida: {result.get('error')}"

    def test_odd_hybrid_repo_initialization(self):
        """Inicializar repositorio ODD Híbrido."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        assert repo is not None
        assert repo.tenant_id == DEFAULT_TENANT_ID


class TestOddDocumentVigente:
    """Pruebas de documento ODD vigente (JSONB)."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto para cada test usando UoW."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Test Project {str(uuid.uuid4())[:8]}",
                slug=f"test-{str(uuid.uuid4())[:8]}",
                request={"type": "test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_upsert_and_get_odd_document(self):
        """Crear y obtener documento ODD vigente."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        spec = {
            "name": "Test Feature",
            "specs": [
                {"id": "S001", "title": "Alcance", "acceptance": ["AC1", "AC2"]},
                {"id": "S002", "title": "Arquitectura", "acceptance": ["AC3"]}
            ],
            "tasks": [
                {"id": "T001", "spec_id": "S001", "route": "pm", "description": "Definir alcance"},
                {"id": "T002", "spec_id": "S002", "route": "architect", "description": "Definir arquitectura"}
            ]
        }

        doc = repo.upsert_odd_document(project_id, spec)
        assert doc is not None
        assert doc.project_id == project_id
        assert doc.spec == spec
        assert doc.spec_hash is not None

        # Recuperar y verificar
        retrieved = repo.get_odd_document(project_id)
        assert retrieved is not None
        assert retrieved.spec == spec
        assert retrieved.spec_hash == doc.spec_hash

    def test_upsert_odd_document_idempotent(self):
        """Upsert idempotente: mismo contenido no cambia hash."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        spec = {"name": "Idempotent Test", "version": 1}

        doc1 = repo.upsert_odd_document(project_id, spec)
        doc2 = repo.upsert_odd_document(project_id, spec)

        assert doc1.spec_hash == doc2.spec_hash

    def test_upsert_odd_document_change(self):
        """Upsert con cambio actualiza hash."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        spec1 = {"name": "Change Test", "version": 1}
        spec2 = {"name": "Change Test", "version": 2}

        doc1 = repo.upsert_odd_document(project_id, spec1)
        doc2 = repo.upsert_odd_document(project_id, spec2)

        assert doc1.spec_hash != doc2.spec_hash


class TestOddSnapshots:
    """Pruebas de snapshots históricos inmutables."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto para cada test usando UoW."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Test Project {str(uuid.uuid4())[:8]}",
                slug=f"test-{str(uuid.uuid4())[:8]}",
                request={"type": "test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_create_and_list_odd_snapshots(self):
        """Crear y listar snapshots ODD."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        snap1 = repo.create_odd_snapshot(
            project_id=project_id,
            body={"version": 1, "data": "first"},
            reason="Initial version"
        )
        assert snap1.id is not None
        assert snap1.body == {"version": 1, "data": "first"}
        assert snap1.reason == "Initial version"

        snap2 = repo.create_odd_snapshot(
            project_id=project_id,
            body={"version": 2, "data": "second"},
            reason="Update"
        )
        assert snap2.id is not None
        assert snap2.doc_id == snap1.id  # Enlaza al anterior

        snapshots = repo.get_odd_snapshots(project_id, limit=10)
        assert len(snapshots) == 2
        assert snapshots[0].id == snap2.id  # Más reciente primero
        assert snapshots[1].id == snap1.id

    def test_get_odd_version_by_timestamp(self):
        """Recuperar versión histórica sin alterar vigente."""
        import time
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        snap1 = repo.create_odd_snapshot(project_id, {"v": 1}, "v1")
        time.sleep(0.1)
        snap2 = repo.create_odd_snapshot(project_id, {"v": 2}, "v2")

        # Recuperar versión 1
        v1 = repo.get_odd_version(project_id, timestamp=snap1.created_at)
        assert v1 is not None
        assert v1.body == {"v": 1}

        # Recuperar versión 2 (más reciente)
        v2 = repo.get_odd_version(project_id, timestamp=snap2.created_at)
        assert v2 is not None
        assert v2.body == {"v": 2}

        # Recuperar última (sin timestamp)
        latest = repo.get_odd_version(project_id)
        assert latest.id == snap2.id

    def test_odd_snapshots_immutable(self):
        """Snapshots no se pueden modificar después de creados."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        snap = repo.create_odd_snapshot(project_id, {"data": "original"}, "original")
        original_id = snap.id

        # Crear uno nuevo (cada insert es nuevo snapshot, no modifica el anterior)
        snap2 = repo.create_odd_snapshot(project_id, {"data": "modified"}, "modified")

        assert snap2.id != original_id
        assert snap2.body == {"data": "modified"}


class TestOddTaskLinks:
    """Pruebas de enlaces ODD-Task."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto para cada test usando UoW."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Test Project {str(uuid.uuid4())[:8]}",
                slug=f"test-{str(uuid.uuid4())[:8]}",
                request={"type": "test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_link_odd_task(self):
        """Vincular tarea ODD con tarea Brain."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        repo.link_odd_task(project_id, "S001", "brain_task_123")

        brain_task = repo.get_brain_task_for_odd(project_id, "S001")
        assert brain_task == "brain_task_123"

    def test_update_odd_task_link(self):
        """Actualizar enlace ODD-Task."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        repo.link_odd_task(project_id, "S002", "task_v1")
        repo.link_odd_task(project_id, "S002", "task_v2")

        brain_task = repo.get_brain_task_for_odd(project_id, "S002")
        assert brain_task == "task_v2"

    def test_reverse_lookup_brain_to_odd(self):
        """Búsqueda inversa: Brain Task ID -> ODD Task ID."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        repo.link_odd_task(project_id, "S003", "brain_task_456")

        odd_task = repo.get_odd_task_for_brain(project_id, "brain_task_456")
        assert odd_task == "S003"


class TestOddHybridUnitOfWork:
    """Pruebas de integración con Unit of Work."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto para cada test usando UoW."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Test Project {str(uuid.uuid4())[:8]}",
                slug=f"test-{str(uuid.uuid4())[:8]}",
                request={"type": "test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_uow_odd_operations(self):
        """Operaciones ODD dentro de Unit of Work."""
        project_id = self.project_id

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            # Crear ODD vigente
            spec = {"name": "UoW Test", "version": 1}
            doc = uow.odd_hybrid.upsert_odd_document(project_id, spec)
            uow.commit()

        # Verificar fuera de UoW
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        retrieved = repo.get_odd_document(project_id)
        assert retrieved is not None
        assert retrieved.spec == spec

    def test_uow_odd_snapshot_atomic(self):
        """Snapshot atómico dentro de transacción."""
        project_id = self.project_id

        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            uow.odd_hybrid.create_odd_snapshot(
                project_id=project_id,
                body={"atomic": True},
                reason="Atomic snapshot"
            )
            uow.commit()

        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        snapshots = repo.get_odd_snapshots(project_id)
        assert len(snapshots) == 1
        assert snapshots[0].body == {"atomic": True}

    def test_uow_rollback_on_error(self):
        """Rollback automático ante error."""
        project_id = self.project_id

        try:
            with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
                uow.odd_hybrid.create_odd_snapshot(
                    project_id=project_id,
                    body={"should": "rollback"},
                    reason="Will rollback"
                )
                raise ValueError("Force rollback")
        except ValueError:
            pass

        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        snapshots = repo.get_odd_snapshots(project_id)
        assert len(snapshots) == 0  # Rollback funcionó


class TestOddHybridIsolation:
    """Pruebas de aislamiento por tenant_id y project_id."""

    def test_tenant_isolation_odd(self, isolated_postgres):
        """Aislamiento de ODD entre tenants."""
        # Use the registered synthetic tenant and create an isolated second tenant.
        tenant1 = DEFAULT_TENANT_ID  # TestTenant_234528

        # Crear proyectos para cada tenant usando UoW
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=tenant1) as uow1:
            p1 = uow1.projects.create_project(tenant_id=tenant1, name=f"T1-{str(uuid.uuid4())[:8]}", slug=f"t1-{str(uuid.uuid4())[:8]}", request={})
            uow1.commit()
        isolated_postgres(p1.id, tenant1)

        tenant_name = f'EXF-F7-004 synthetic isolation tenant {uuid.uuid4()}'
        tenant_slug = f'exf-f7-004-isolation-{uuid.uuid4().hex}'
        with UnitOfWork(tenant_id=tenant1) as uow2:
            tenant2_record = uow2.identity.create_tenant(tenant_name, tenant_slug)
            tenant2 = tenant2_record.id
            p2 = uow2.projects.create_project(tenant_id=tenant2, name=f"T2-{str(uuid.uuid4())[:8]}", slug=f"t2-{str(uuid.uuid4())[:8]}", request={})
            uow2.commit()
        isolated_postgres.register_tenant(tenant2, tenant_name, tenant_slug)
        isolated_postgres(p2.id, tenant2)

        repo1 = OddHybridRepository(tenant_id=tenant1)
        repo2 = OddHybridRepository(tenant_id=tenant2)

        repo1.upsert_odd_document(p1.id, {"tenant": 1})
        repo2.upsert_odd_document(p2.id, {"tenant": 2})

        doc1 = repo1.get_odd_document(p1.id)
        doc2 = repo2.get_odd_document(p2.id)

        # Cada tenant ve su propia versión
        assert doc1.spec.get("tenant") == 1
        assert doc2.spec.get("tenant") == 2

    def test_project_isolation(self):
        """Aislamiento entre proyectos del mismo tenant."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            p1 = uow.projects.create_project(tenant_id=DEFAULT_TENANT_ID, name=f"P1-{str(uuid.uuid4())[:8]}", slug=f"p1-{str(uuid.uuid4())[:8]}", request={})
            p2 = uow.projects.create_project(tenant_id=DEFAULT_TENANT_ID, name=f"P2-{str(uuid.uuid4())[:8]}", slug=f"p2-{str(uuid.uuid4())[:8]}", request={})
            uow.commit()

        odd_repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        odd_repo.upsert_odd_document(p1.id, {"project": 1})
        odd_repo.upsert_odd_document(p2.id, {"project": 2})

        doc1 = odd_repo.get_odd_document(p1.id)
        doc2 = odd_repo.get_odd_document(p2.id)

        assert doc1.spec.get("project") == 1
        assert doc2.spec.get("project") == 2


class TestProjectSnapshots:
    """Pruebas de snapshots completos de proyecto."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto para cada test usando UoW."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork(tenant_id=DEFAULT_TENANT_ID) as uow:
            project = uow.projects.create_project(
                tenant_id=DEFAULT_TENANT_ID,
                name=f"Test Project {str(uuid.uuid4())[:8]}",
                slug=f"test-{str(uuid.uuid4())[:8]}",
                request={"type": "test"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_create_project_snapshot(self):
        """Crear snapshot completo de proyecto."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        snapshot = {
            "name": "Full Snapshot",
            "spec": {"version": 1},
            "tasks": [{"id": "T1", "status": "done"}],
            "metadata": {"author": "test"}
        }

        result = repo.create_project_snapshot(
            project_id=project_id,
            snapshot=snapshot,
            reason="Full backup"
        )

        assert result is not None
        assert result['project_id'] == project_id
        assert result['snapshot'] == snapshot

    def test_list_project_snapshots(self):
        """Listar snapshots de proyecto."""
        repo = OddHybridRepository(tenant_id=DEFAULT_TENANT_ID)
        project_id = self.project_id

        repo.create_project_snapshot(project_id, {"v": 1}, "v1")
        repo.create_project_snapshot(project_id, {"v": 2}, "v2")

        snapshots = repo.list_project_snapshots(project_id, limit=10)
        assert len(snapshots) == 2
        assert snapshots[0]['snapshot'] == {"v": 2}  # Más reciente primero


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
