"""Pruebas simples funcionales para F3-001: Brain PostgreSQL.

Versión simplificada para validar la funcionalidad básica.
"""

import os
import sys
import pytest
import uuid
import json

# Cargar variables de entorno desde .env
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())

load_env()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tenant existente para pruebas
DEFAULT_TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


class TestBrainPostgresConnection:
    """Pruebas de conexión y conectividad."""

    def test_postgres_connection(self):
        """Prueba 1: Verificar conectividad a PostgreSQL."""
        from infrastructure.db import health_check, test_connection
        result = test_connection()
        assert result['success'], f"Conexión fallida: {result.get('error')}"
        print(f"Conectado a: {result.get('database')} como {result.get('user')}")

    def test_brain_initialization(self):
        """Prueba 2: Inicializar Brain PostgreSQL."""
        from brain.postgres_store import Brain
        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        assert brain is not None

    def test_brain_with_tenant(self):
        """Prueba 3: Brain con tenant_id."""
        from brain.postgres_store import Brain
        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        assert brain.tenant_id == DEFAULT_TENANT_ID


class TestBrainProjects:
    """Pruebas de proyectos."""

    def test_add_and_get_project(self):
        """Prueba 4: Agregar y obtener proyecto."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Test Project", "# Test\n\nRequest content")
        project = brain.get_project(project_id)

        assert project is not None
        assert str(project.id) == project_id
        assert project.name.startswith("Test Project")

    def test_update_project_status(self):
        """Prueba 5: Actualizar estado de proyecto."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Status Test", "Request")
        brain.set_project_status(project_id, 'planned')

        project = brain.get_project(project_id)
        assert project.status == 'planned'


class TestBrainTasks:
    """Pruebas de tareas."""

    def test_add_and_list_tasks(self):
        """Prueba 6: Agregar y listar tareas."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        brain.add_project(project_id, "Task Test", "Request")
        brain.add_task(task_id, project_id, "Implement feature", "developer", max_attempts=3)

        tasks = brain.tasks(project_id)
        assert len(tasks) == 1
        assert str(tasks[0]['id']) == task_id
        assert tasks[0]['status'] == 'pending'

    def test_update_task_status(self):
        """Prueba 7: Actualizar estado de tarea."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())

        brain.add_project(project_id, "Task Test", "Request")
        brain.add_task(task_id, project_id, "Test task", "developer")

        brain.update_task(task_id, 'running')
        tasks = brain.tasks(project_id)
        assert tasks[0]['status'] == 'running'


class TestBrainKnowledge:
    """Pruebas de memoria (knowledge)."""

    def test_remember_and_recall(self):
        """Prueba 8: Recordar y recuperar conocimiento."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Test Project", "Request")

        knowledge_id = brain.remember(
            kind='spec',
            title='API Design',
            body='API specification for authentication',
            source='Architect',
            project_id=project_id
        )
        assert knowledge_id is not None  # Ahora retorna UUID

        results = brain.recall('API', project_id=project_id, limit=5)
        assert len(results) >= 1
        assert any('API' in r.get('title', '') for r in results)


class TestBrainOddDocuments:
    """Pruebas de documentos ODD."""

    def test_save_and_get_odd_document(self):
        """Prueba 9: Guardar y obtener documento ODD."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())
        doc_body = json.dumps({"name": "Test Feature", "specs": []})

        brain.add_project(project_id, "Test Feature", "Request")
        brain.save_odd_document(project_id, doc_body)

        retrieved = brain.get_odd_document(project_id)
        assert retrieved == doc_body

    def test_persist_odd_document(self):
        """Prueba 10: Persistir documento ODD con snapshots."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Test Feature", "Request")

        # Primer guardado
        changed1 = brain.persist_odd_document(
            project_id,
            "Feature: Test",
            json.dumps({"name": "v1"})
        )
        assert changed1 is True

        # Mismo contenido - no cambia
        changed2 = brain.persist_odd_document(
            project_id,
            "Feature: Test",
            json.dumps({"name": "v1"})
        )
        assert changed2 is False

        # Nuevo contenido - cambia
        changed3 = brain.persist_odd_document(
            project_id,
            "Feature: Test",
            json.dumps({"name": "v2"})
        )
        assert changed3 is True


class TestBrainOddTaskLinks:
    """Pruebas de enlaces ODD-Task."""

    def test_link_and_retrieve(self):
        """Prueba 11: Crear y recuperar enlace ODD-Task."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Test Feature", "Request")

        brain.link_odd_task(project_id, "S001", "task_abc123")

        brain_task = brain.brain_task_for_odd(project_id, "S001")
        assert brain_task == "task_abc123"

    def test_update_link(self):
        """Prueba 12: Actualizar enlace ODD-Task."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Test Feature", "Request")

        brain.link_odd_task(project_id, "S002", "task_v1")
        # Leer el primero
        first = brain.brain_task_for_odd(project_id, "S002")
        # Actualizar
        brain.link_odd_task(project_id, "S002", "task_v2")
        # Leer el segundo
        second = brain.brain_task_for_odd(project_id, "S002")
        assert second == "task_v2"


class TestBrainIdempotency:
    """Pruebas de idempotencia."""

    def test_add_project_idempotent(self):
        """Prueba 13: Agregar mismo proyecto no duplica."""
        from brain.postgres_store import Brain

        brain = Brain(tenant_id=DEFAULT_TENANT_ID)
        project_id = str(uuid.uuid4())

        brain.add_project(project_id, "Test", "Request 1")
        brain.add_project(project_id, "Test Updated", "Request 2")

        project = brain.get_project(project_id)
        assert project.name.startswith("Test Updated")


class TestBrainUnitOfWork:
    """Pruebas de integración con Unit of Work."""

    def test_uow_operations(self):
        """Prueba 14: Operaciones en Unit of Work."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.repositories import ProjectsRepository
        import uuid

        tenant_id = DEFAULT_TENANT_ID
        project_id = str(uuid.uuid4())

        with UnitOfWork(tenant_id=tenant_id) as uow:
            uow.projects.create_project(
                tenant_id=tenant_id,
                name=f"UoW Test {str(uuid.uuid4())[:8]}",
                slug=f"test-uow-{str(uuid.uuid4())[:8]}",
                description="Test",
                request={"type": "test"}
            )
            uow.commit()

        # Verificar
        repo = ProjectsRepository(tenant_id=tenant_id)
        projects = repo.list_projects(tenant_id)  # Usar list_projects
        assert len(projects) > 0
        assert any(p.name.startswith("UoW Test") for p in projects)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])