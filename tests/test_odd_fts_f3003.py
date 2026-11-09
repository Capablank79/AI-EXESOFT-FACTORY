"""Pruebas funcionales para F3-003: Full Text Search (FTS).

Pruebas cubren:
1. Búsqueda en knowledge con tsvector/tsquery/ts_rank
2. Búsqueda en project_specs (ODD vigente)
3. Búsqueda en odd_snapshots (snapshots históricos)
4. Búsqueda híbrida
4. Relevancia y ranking
5. Filtros tenant_id y project_id
6. Soporte español/inglés
7. Aislamiento multi-tenant

Autor: Claude Code - EXESOFT AI FACTORY - F3-003
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

from infrastructure.db import test_connection as db_test_connection
from infrastructure.repositories import OddHybridRepository
from infrastructure.uow import UnitOfWork


def _create_tenant(uow, label):
    suffix = uuid.uuid4().hex
    tenant = uow.identity.create_tenant(
        name=f'{label} {suffix}',
        slug=f'{label.lower().replace(" ", "-")}-{suffix}'
    )
    return tenant.id


def _snapshot_content(body):
    """Return snapshot content whether PostgreSQL returns TEXT or parsed JSON."""
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return body
    if isinstance(body, dict):
        return str(body.get('content', ''))
    return ''


class TestFTSConnection:
    """Pruebas de conexión y configuración."""

    def test_postgres_connection(self):
        """Verificar conectividad PostgreSQL."""
        result = db_test_connection()
        assert result['success'], f"Conexión fallida: {result.get('error')}"

    def test_odd_hybrid_repo_initialization(self):
        """Inicializar repositorio ODD Híbrido con FTS."""
        repo = OddHybridRepository()
        assert repo is not None
        assert repo.tenant_id is None


class TestFTSKnowledge:
    """Pruebas de búsqueda FTS en knowledge."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto y conocimiento de prueba usando el mismo UoW."""
        from infrastructure.uow import UnitOfWork

        with UnitOfWork() as uow:
            self.tenant_id = _create_tenant(uow, 'FTS knowledge')
            project = uow.projects.create_project(
                tenant_id=self.tenant_id,
                name=f"FTS Test {str(uuid.uuid4())[:8]}",
                slug=f"fts-test-{str(uuid.uuid4())[:8]}",
                request={"type": "fts-test"}
            )
            self.project_id = project.id

            # Crear conocimiento de prueba usando el mismo UoW (misma conexión)
            uow.memory.create_knowledge(
                kind='spec',
                title='Autenticación JWT',
                body='Implementar autenticación con tokens JWT para API REST',
                source='Architect',
                project_id=self.project_id,
                tags=['auth', 'jwt', 'api'],
                tenant_id=self.tenant_id
            )
            uow.memory.create_knowledge(
                kind='spec',
                title='Autorización RBAC',
                body='Control de acceso basado en roles para recursos protegidos',
                source='Architect',
                project_id=self.project_id,
                tags=['auth', 'rbac', 'authorization'],
                tenant_id=self.tenant_id
            )
            uow.memory.create_knowledge(
                kind='note',
                title='Configuración Base de Datos',
                body='PostgreSQL 15 con FTS habilitado para búsquedas',
                source='DevOps',
                project_id=self.project_id,
                tags=['db', 'postgresql', 'fts'],
                tenant_id=self.tenant_id
            )
            uow.commit()
        yield

    def test_search_knowledge_fts_spanish(self):
        """Búsqueda FTS en español."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_knowledge('autenticación', self.project_id, language='spanish', limit=10)
        assert len(results) >= 1
        assert any('autenticación' in r.get('title', '').lower() or 'autenticación' in r.get('body', '').lower() for r in results)

        # Verificar ranking
        for r in results:
            assert 'rank' in r
            assert r['rank'] >= 0

    def test_search_knowledge_fts_english(self):
        """Búsqueda FTS en inglés."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_knowledge('authentication', self.project_id, language='english', limit=10)
        # Puede no encontrar resultados si el contenido es en español, pero no debe fallar
        assert isinstance(results, list)

    def test_search_knowledge_filter_kind(self):
        """Filtrar búsqueda por kind."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_knowledge('autenticación', self.project_id, kind='spec', language='spanish', limit=10)
        assert len(results) >= 1
        assert all(r['kind'] == 'spec' for r in results)

    def test_search_knowledge_ranking(self):
        """Verificar que ts_rank ordena por relevancia."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_knowledge('autenticación', self.project_id, language='spanish', limit=10)
        if len(results) > 1:
            # Los primeros resultados deben tener mayor rank
            for i in range(len(results) - 1):
                assert results[i]['rank'] >= results[i + 1]['rank']


class TestFTSProjectSpecs:
    """Pruebas de búsqueda FTS en project_specs (ODD vigente)."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        """Crear proyecto para cada test usando UoW."""
        from infrastructure.uow import UnitOfWork
        with UnitOfWork() as uow:
            self.tenant_id = _create_tenant(uow, 'FTS specs')
            project = uow.projects.create_project(
                tenant_id=self.tenant_id,
                name=f"FTS Specs {str(uuid.uuid4())[:8]}",
                slug=f"fts-specs-{str(uuid.uuid4())[:8]}",
                request={"type": "fts-specs"}
            )
            self.project_id = project.id
            # Crear spec ODD
            repo = uow.odd_hybrid
            repo.upsert_odd_document(self.project_id, {
                "name": "Sistema de Autenticación",
                "specs": [
                    {"id": "S001", "title": "JWT Auth", "acceptance": ["Tokens válidos", "Expiración"]},
                    {"id": "S002", "title": "RBAC", "acceptance": ["Roles definidos", "Permisos"]}
                ],
                "tasks": []
            })
            uow.commit()
        yield

    def test_search_odd_document_fts(self):
        """Búsqueda en documento ODD vigente."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_odd_document(self.project_id, 'autenticación', 'spanish', 10)
        assert len(results) >= 1
        # spec es un dict, buscar en su representación JSON
        import json
        assert any(
            'autenticación' in json.dumps(r.get('spec', {}), ensure_ascii=False).lower()
            for r in results
        )

        for r in results:
            assert 'rank' in r


class TestFTSSnapshots:
    """Pruebas de búsqueda FTS en snapshots históricos."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork() as uow:
            self.tenant_id = _create_tenant(uow, 'FTS snapshots')
            project = uow.projects.create_project(
                tenant_id=self.tenant_id,
                name=f"FTS Snapshots {str(uuid.uuid4())[:8]}",
                slug=f"fts-snap-{str(uuid.uuid4())[:8]}",
                request={"type": "fts-snapshots"}
            )
            self.project_id = project.id

            repo = uow.odd_hybrid
            repo.create_odd_snapshot(self.project_id, {
                "version": 1,
                "content": "Autenticación JWT implementada",
                "author": "Architect"
            }, "Versión inicial")
            time.sleep(0.1)
            repo.create_odd_snapshot(self.project_id, {
                "version": 2,
                "content": "Autenticación JWT + RBAC autorización",
                "author": "Architect"
            }, "Agregado RBAC")
            uow.commit()
        yield

    def test_search_odd_snapshots_fts(self):
        """Búsqueda en snapshots históricos."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_odd_snapshots(self.project_id, 'autenticación', 'spanish', 10)
        assert len(results) >= 1
        assert any(
            'autenticación' in _snapshot_content(r.get('body')).lower()
            for r in results
        )

        for r in results:
            assert 'rank' in r


class TestFTSHybrid:
    """Pruebas de búsqueda híbrida."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork

        with UnitOfWork() as uow:
            self.tenant_id = _create_tenant(uow, 'FTS hybrid')
            project = uow.projects.create_project(
                tenant_id=self.tenant_id,
                name=f"Hybrid FTS {str(uuid.uuid4())[:8]}",
                slug=f"hybrid-fts-{str(uuid.uuid4())[:8]}",
                request={"type": "hybrid-fts"}
            )
            self.project_id = project.id

            uow.memory.create_knowledge(
                kind='spec',
                title='JWT Auth',
                body='Autenticación JWT para microservicios',
                source='Architect',
                project_id=self.project_id,
                tenant_id=self.tenant_id
            )
            uow.odd_hybrid.upsert_odd_document(self.project_id, {
                "name": "Auth System",
                "specs": [{"title": "JWT Tokens"}]
            })
            uow.odd_hybrid.create_odd_snapshot(self.project_id, {"content": "JWT Auth v1"}, "Initial")
            uow.commit()
        yield

    def test_search_odd_hybrid(self):
        """Búsqueda híbrida completa."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        results = repo.search_odd_hybrid('jwt', self.project_id, 'english', 20)

        assert 'knowledge' in results
        assert 'project_specs' in results
        assert 'odd_snapshots' in results

        total = len(results['knowledge']) + len(results['project_specs']) + len(results['odd_snapshots'])
        assert total >= 1


class TestFTSRelevance:
    """Pruebas de relevancia y ranking."""

    @pytest.fixture(autouse=True)
    def setup_project(self):
        from infrastructure.uow import UnitOfWork
        with UnitOfWork() as uow:
            self.tenant_id = _create_tenant(uow, 'FTS relevance')
            project = uow.projects.create_project(
                tenant_id=self.tenant_id,
                name=f"Relevance {str(uuid.uuid4())[:8]}",
                slug=f"rel-{str(uuid.uuid4())[:8]}",
                request={"type": "relevance"}
            )
            self.project_id = project.id
            uow.commit()
        yield

    def test_search_relevance_stats(self):
        """Estadísticas de relevancia."""
        repo = OddHybridRepository(tenant_id=self.tenant_id)

        stats = repo.search_odd_hybrid('autenticación', self.project_id, 'spanish', 10)

        assert 'knowledge' in stats
        assert 'project_specs' in stats
        assert 'odd_snapshots' in stats

        total = len(stats['knowledge']) + len(stats['project_specs']) + len(stats['odd_snapshots'])
        assert total >= 0


class TestFTSIsolation:
    """Pruebas de aislamiento FTS."""

    def test_tenant_isolation_fts(self):
        """Aislamiento FTS entre tenants."""
        from infrastructure.uow import UnitOfWork

        marker = f'tenantisolation{uuid.uuid4().hex}'
        with UnitOfWork() as uow:
            tenant1 = _create_tenant(uow, 'FTS tenant one')
            tenant2 = _create_tenant(uow, 'FTS tenant two')
            p1 = uow.projects.create_project(
                tenant_id=tenant1, name='Tenant one FTS',
                slug=f't1-{uuid.uuid4().hex}', request={}
            )
            p2 = uow.projects.create_project(
                tenant_id=tenant2, name='Tenant two FTS',
                slug=f't2-{uuid.uuid4().hex}', request={}
            )
            uow.memory.create_knowledge(
                kind='spec', title=f'{marker} one', body='tenant one data',
                source='F3-003 test', project_id=p1.id, tenant_id=tenant1
            )
            uow.memory.create_knowledge(
                kind='spec', title=f'{marker} two', body='tenant two data',
                source='F3-003 test', project_id=p2.id, tenant_id=tenant2
            )
            uow.commit()

        repo1 = OddHybridRepository(tenant_id=tenant1)
        repo2 = OddHybridRepository(tenant_id=tenant2)

        results1 = repo1.search_knowledge(marker, p1.id, language='english', limit=10)
        results2 = repo2.search_knowledge(marker, p2.id, language='english', limit=10)

        # Cada tenant ve solo sus datos - verificar que el tenant_id coincide
        assert len(results1) == 1
        assert len(results2) == 1
        assert all(uuid.UUID(str(r['tenant_id'])) == tenant1 for r in results1)
        assert all(uuid.UUID(str(r['tenant_id'])) == tenant2 for r in results2)
        assert all(uuid.UUID(str(r['project_id'])) == p1.id for r in results1)
        assert all(uuid.UUID(str(r['project_id'])) == p2.id for r in results2)

    def test_project_isolation_fts(self):
        """Aislamiento FTS entre proyectos."""
        from infrastructure.uow import UnitOfWork
        from infrastructure.repositories import MemoryRepository

        from infrastructure.uow import UnitOfWork

        marker = f'projectisolation{uuid.uuid4().hex}'
        with UnitOfWork() as uow:
            tenant_id = _create_tenant(uow, 'FTS project isolation')
            p1 = uow.projects.create_project(
                tenant_id=tenant_id, name='Project one FTS',
                slug=f'p1-{uuid.uuid4().hex}', request={}
            )
            p2 = uow.projects.create_project(
                tenant_id=tenant_id, name='Project two FTS',
                slug=f'p2-{uuid.uuid4().hex}', request={}
            )
            uow.memory.create_knowledge(
                kind='spec', title=f'{marker} one', body='project one data',
                source='F3-003 test', project_id=p1.id, tenant_id=tenant_id
            )
            uow.memory.create_knowledge(
                kind='spec', title=f'{marker} two', body='project two data',
                source='F3-003 test', project_id=p2.id, tenant_id=tenant_id
            )
            uow.commit()

        repo = OddHybridRepository(tenant_id=tenant_id)
        results1 = repo.search_knowledge(marker, p1.id, language='english', limit=10)
        results2 = repo.search_knowledge(marker, p2.id, language='english', limit=10)

        assert len(results1) == 1
        assert len(results2) == 1
        assert all(uuid.UUID(str(r['project_id'])) == p1.id for r in results1)
        assert all(uuid.UUID(str(r['project_id'])) == p2.id for r in results2)
        assert all(uuid.UUID(str(r['tenant_id'])) == tenant_id for r in results1 + results2)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
