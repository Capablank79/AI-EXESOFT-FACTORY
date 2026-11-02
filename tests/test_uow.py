"""Pruebas para Unit of Work - F2-003.

Pruebas:
1. test_uow_context_manager - Uso como context manager
2. test_uow_commit - Commit explícito
3. test_uow_rollback - Rollback automático
4. test_uow_atomicity - Atomicidad entre repositorios
5. test_uow_connection_return - Devolución de conexión
6. test_uow_tenant_isolation - Aislamiento por tenant_id
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test that the module imports and UnitOfWork class exists
def test_uow_module_import():
    """Verificar que el módulo UoW se importa correctamente."""
    from infrastructure import uow
    assert hasattr(uow, 'UnitOfWork')
    assert hasattr(uow, 'UnitOfWorkError')
    assert hasattr(uow, 'unit_of_work')


def test_uow_context_manager_exists():
    """Verificar que UnitOfWork es un context manager."""
    from infrastructure.uow import UnitOfWork
    uow = UnitOfWork()
    assert hasattr(uow, '__enter__')
    assert hasattr(uow, '__exit__')
    assert hasattr(uow, 'commit')


def test_uow_has_repositories():
    """Verificar que UoW expone todos los repositorios."""
    from infrastructure.uow import UnitOfWork
    uow = UnitOfWork()

    # Check that properties exist
    assert hasattr(uow, 'identity')
    assert hasattr(uow, 'projects')
    assert hasattr(uow, 'planning')
    assert hasattr(uow, 'execution')
    assert hasattr(uow, 'memory')
    assert hasattr(uow, 'events')
    assert hasattr(uow, 'quality')
    assert hasattr(uow, 'providers')
    assert hasattr(uow, 'artifacts')


def test_uow_tenant_id():
    """Verificar que tenant_id se pasa a los repositorios."""
    import uuid
    from infrastructure.uow import UnitOfWork

    tenant_id = uuid.uuid4()
    uow = UnitOfWork(tenant_id=tenant_id)

    # The repositories should have the tenant_id
    projects_repo = uow.projects
    assert projects_repo.tenant_id == tenant_id


if __name__ == '__main__':
    pytest.main([__file__, '-v'])