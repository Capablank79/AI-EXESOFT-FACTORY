"""Fixtures for explicitly isolated PostgreSQL integration tests."""
import os
import re

import pytest


_SAFE_TEST_DB = re.compile(r'^exesoft_test_[a-z0-9_]{3,48}$')
_PROTECTED_DATABASES = {
    'exesoft_factory',
    'exesoft_backup_fixture_f7003_20261105',
    'exesoft_restore_f7003_20261105',
}
_TEST_TENANT_ID = 'b66f93cd-2f3a-4215-a2ac-93a59a84b4d2'
_TEST_TENANT = ('EXF-F7-004 synthetic test tenant', 'exf-f7-004-synthetic-test', True)
_POSTGRES_ENV_KEYS = (
    'POSTGRES_DB', 'EXESOFT_TEST_POSTGRES_DB', 'EXESOFT_TEST_DATABASE_REQUIRED',
    'EXESOFT_BACKUP_TEST_ENABLED',
    'POSTGRES_APP_USER', 'POSTGRES_APP_PASSWORD', 'POSTGRES_MIGRATOR_USER',
    'POSTGRES_MIGRATOR_PASSWORD', 'POSTGRES_HOST', 'POSTGRES_PORT',
)

# Modules that may exercise the live PostgreSQL repositories. The guard is
# installed for every collected test in these modules, including tests that
# predate the explicit isolated_postgres fixture.
_POSTGRES_INTEGRATION_MODULES = {
    'test_brain_f3001_simple.py',
    'test_brain_postgres.py',
    'test_db_connection.py',
    'test_f3004_consistency.py',
    'test_f4001_runtime_postgres.py',
    'test_f5001_model_gateway.py',
    'test_f5003_handoffs.py',
    'test_f5004_agent_flow.py',
    'test_f6001_sandy_runtime.py',
    'test_f6003_quality_gate.py',
    'test_f6004_sandy_artifact_gate.py',
    'test_f7001_factory_cli.py',
    'test_f7002_factory_api.py',
    'test_f7004_observability.py',
    'test_f8001_factory_e2e.py',
    'test_f8002_concurrency.py',
    'test_f8003_recovery.py',
    'test_odd_fts_f3003.py',
    'test_odd_hybrid_f3002.py',
    'test_uow.py',
}
_POSTGRES_TEST_SOURCE_MARKERS = (
    'PostgresRuntime', 'UnitOfWork', 'psycopg.connect',
    'get_connection(', 'infrastructure.db',
)


def pytest_collection_modifyitems(items):
    """Automatically guard new tests that directly use PostgreSQL interfaces."""
    for item in items:
        path = getattr(item, 'path', None)
        if path is None:
            continue
        module_name = getattr(path, 'name', '')
        if module_name in _POSTGRES_INTEGRATION_MODULES:
            continue
        try:
            source = path.read_text(encoding='utf-8')
        except OSError:
            continue
        if any(marker in source for marker in _POSTGRES_TEST_SOURCE_MARKERS):
            _POSTGRES_INTEGRATION_MODULES.add(module_name)


@pytest.fixture(autouse=True)
def preserve_postgres_environment():
    """Prevent a test's PostgreSQL settings or cached pool from leaking to peers."""
    saved = {key: os.environ.get(key) for key in _POSTGRES_ENV_KEYS}
    from infrastructure import db as db_module
    saved_pool_config = db_module._pool_config
    yield
    db_module.reset_pool()
    db_module._pool_config = saved_pool_config
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def postgres_test_database_guard(request, preserve_postgres_environment):
    """Fail closed and verify the live DB before PostgreSQL test modules run."""
    module_name = getattr(request.node, 'path', None)
    module_name = getattr(module_name, 'name', '')
    if module_name not in _POSTGRES_INTEGRATION_MODULES:
        yield
        return

    expected = 'exesoft_test_f7004'
    if (os.environ.get('POSTGRES_DB') != expected
            or os.environ.get('EXESOFT_TEST_POSTGRES_DB') != expected
            or os.environ.get('EXESOFT_BACKUP_TEST_ENABLED') != '0'):
        pytest.fail(
            'PostgreSQL integration tests require POSTGRES_DB and '
            'EXESOFT_TEST_POSTGRES_DB=exesoft_test_f7004 and '
            'EXESOFT_BACKUP_TEST_ENABLED=0'
        )

    from infrastructure import db

    previous_guard = os.environ.get('EXESOFT_TEST_DATABASE_REQUIRED')
    os.environ['EXESOFT_TEST_DATABASE_REQUIRED'] = '1'
    db.reset_pool()
    try:
        if db.get_config().db != expected:
            pytest.fail('Refusing PostgreSQL test: configured database identity mismatch')
        with db.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT current_database()')
                actual = cursor.fetchone()[0]
                if actual != expected:
                    pytest.fail('Refusing PostgreSQL test: connected database identity mismatch')
                cursor.execute(
                    'SELECT name, slug, is_active FROM tenants WHERE id = %s',
                    (_TEST_TENANT_ID,),
                )
                tenant = cursor.fetchone()
        if tenant != _TEST_TENANT:
            pytest.fail('Refusing PostgreSQL test: synthetic tenant marker mismatch')
        yield
    finally:
        db.reset_pool()
        if previous_guard is None:
            os.environ.pop('EXESOFT_TEST_DATABASE_REQUIRED', None)
        else:
            os.environ['EXESOFT_TEST_DATABASE_REQUIRED'] = previous_guard


@pytest.fixture
def isolated_postgres(postgres_test_database_guard):
    """Require a preprovisioned test DB, verify its identity, and clean test projects."""
    database = os.environ.get('EXESOFT_TEST_POSTGRES_DB', '')
    if not _SAFE_TEST_DB.fullmatch(database) or database in _PROTECTED_DATABASES:
        pytest.fail('Set EXESOFT_TEST_POSTGRES_DB to a preprovisioned exesoft_test_* database')
    if os.environ.get('POSTGRES_DB') != database:
        pytest.fail('POSTGRES_DB and EXESOFT_TEST_POSTGRES_DB must both equal exesoft_test_f7004')

    from infrastructure import db

    previous_guard = os.environ.get('EXESOFT_TEST_DATABASE_REQUIRED')
    os.environ['EXESOFT_TEST_DATABASE_REQUIRED'] = '1'
    db.reset_pool()
    created_projects = []
    created_tenants = []

    def verify_database():
        if db.get_config().db != database:
            raise RuntimeError('Refusing PostgreSQL test operation: configured database mismatch')
        with db.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT current_database()')
                actual = cursor.fetchone()[0]
                if actual == database:
                    cursor.execute(
                        'SELECT name, slug, is_active FROM tenants WHERE id = %s',
                        (_TEST_TENANT_ID,),
                    )
                    tenant = cursor.fetchone()
        if actual != database:
            raise RuntimeError('Refusing PostgreSQL test operation: connected database mismatch')
        if tenant != _TEST_TENANT:
            raise RuntimeError('Refusing PostgreSQL test operation: synthetic tenant fixture mismatch')

    def register_project(project_id, tenant_id=_TEST_TENANT_ID):
        created_projects.append((str(project_id), str(tenant_id)))

    def register_tenant(tenant_id, name, slug):
        created_tenants.append((str(tenant_id), name, slug))

    register_project.register_tenant = register_tenant

    try:
        verify_database()
        yield register_project
    finally:
        try:
            if created_projects:
                with db.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute('SELECT current_database()')
                        if cursor.fetchone()[0] != database or db.get_config().db != database:
                            raise RuntimeError('Refusing PostgreSQL cleanup: database identity mismatch')
                        cursor.execute(
                            'SELECT name, slug, is_active FROM tenants WHERE id = %s',
                            (_TEST_TENANT_ID,),
                        )
                        if cursor.fetchone() != _TEST_TENANT:
                            raise RuntimeError('Refusing PostgreSQL cleanup: synthetic tenant mismatch')
                        for project_id, tenant_id in created_projects:
                            cursor.execute(
                                'SELECT 1 FROM projects WHERE id = %s AND tenant_id = %s',
                                (project_id, tenant_id),
                            )
                            if cursor.fetchone() is None:
                                continue
                            # Events must be removed before their task/project parents:
                            # their composite FK's SET NULL action conflicts with
                            # event_requires_project_for_task while task_id remains set.
                            cursor.execute(
                                'DELETE FROM events WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM validations WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM evidence WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                "DELETE FROM artifact_dependencies WHERE parent_artifact_id IN "
                                "(SELECT id FROM artifacts WHERE tenant_id = %s AND project_id = %s) "
                                "OR child_artifact_id IN "
                                "(SELECT id FROM artifacts WHERE tenant_id = %s AND project_id = %s)",
                                (tenant_id, project_id, tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM artifact_storage WHERE artifact_id IN '
                                '(SELECT id FROM artifacts WHERE tenant_id = %s AND project_id = %s)',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM artifacts WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM model_requests WHERE tenant_id = %s AND task_id IN '
                                '(SELECT id FROM tasks WHERE tenant_id = %s AND project_id = %s)',
                                (tenant_id, tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM task_snapshots WHERE task_id IN '
                                '(SELECT id FROM tasks WHERE tenant_id = %s AND project_id = %s)',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM task_domains WHERE task_id IN '
                                '(SELECT id FROM tasks WHERE tenant_id = %s AND project_id = %s)',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM task_runs WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM tasks WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute(
                                'DELETE FROM knowledge WHERE tenant_id = %s AND project_id = %s',
                                (tenant_id, project_id),
                            )
                            cursor.execute('DELETE FROM odd_snapshots WHERE project_id = %s', (project_id,))
                            cursor.execute('DELETE FROM odd_documents WHERE project_id = %s', (project_id,))
                            cursor.execute('DELETE FROM orchestrator_state WHERE project_id = %s', (project_id,))
                            cursor.execute('DELETE FROM project_specs WHERE project_id = %s', (project_id,))
                            cursor.execute('DELETE FROM project_snapshots WHERE project_id = %s', (project_id,))
                            cursor.execute(
                                'DELETE FROM projects WHERE id = %s AND tenant_id = %s',
                                (project_id, tenant_id),
                            )
                        for tenant_id, name, slug in created_tenants:
                            if tenant_id == _TEST_TENANT_ID:
                                continue
                            cursor.execute(
                                'DELETE FROM tenants WHERE id = %s AND name = %s AND slug = %s',
                                (tenant_id, name, slug),
                            )
        finally:
            db.reset_pool()
            if previous_guard is None:
                os.environ.pop('EXESOFT_TEST_DATABASE_REQUIRED', None)
            else:
                os.environ['EXESOFT_TEST_DATABASE_REQUIRED'] = previous_guard
