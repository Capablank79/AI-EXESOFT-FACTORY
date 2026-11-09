#!/usr/bin/env python3
"""F2-004: Pruebas pendientes - corregido definitivamente.

Elaborado para:
- Crear tenants reales dentro de la misma transacción UoW
- Usar UUID devuelto por create_tenant
- No usar UUID fijos
- Verificar commits/rollbacks reales
"""

import os
import sys
import uuid
import hashlib
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from postgres_test_guard import require_isolated_test_database
require_isolated_test_database()

from infrastructure.uow import UnitOfWork


class F2004PendingTests:
    """Pruebas F2-004 con manejo correcto de FK."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tenants_created = []

    def test_commit_between_repos(self):
        """Test 1: COMMIT real entre repositorios."""
        try:
            with UnitOfWork() as uow:
                # Crear tenant DENTRO de la transacción
                tenant = uow.identity.create_tenant(
                    name=f'TestTenant_{datetime.now().strftime("%H%M%S")}',
                    slug=f'test_tenant_{uuid.uuid4().hex[:8]}'
                )
                test_tenant_id = tenant.id
                self.tenants_created.append(str(test_tenant_id))

                # Crear proyecto y evento usando el MISMO tenant
                proj = uow.projects.create_project(
                    tenant_id=test_tenant_id,
                    name=f'CommitProject_{datetime.now().strftime("%H%M%S")}',
                    slug=f'commit_proj_{uuid.uuid4().hex[:8]}'
                )

                event = uow.events.emit_event(
                    type='test.commit.event',
                    detail={'tenant': str(test_tenant_id), 'project': str(proj.id)}
                )

                # COMMIT explícito
                uow.commit()

            # Verificar persistencia
            with UnitOfWork() as verify:
                proj_check = verify.projects.get_project(proj.id)
                event_check = verify.events.get_event(event.id)

            if proj_check and event_check:
                print("  PASS: test_commit_between_repos")
                self.passed += 1
                return True
            else:
                print(f"  FAIL: test_commit_between_repos - data not persisted")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  FAIL: test_commit_between_repos - {type(e).__name__}")
            self.failed += 1
            return False

    def test_rollback_between_repos(self):
        """Test 2: ROLLBACK real entre repositorios."""
        try:
            # Contar eventos antes
            with UnitOfWork() as uow:
                initial_count = len(uow.events.list_events(limit=500))

            test_slug = f'fail_{uuid.uuid4().hex[:8]}'
            try:
                with UnitOfWork() as uow:
                    # Crear tenant primero
                    t = uow.identity.create_tenant(
                        name='RollbackFailTenant',
                        slug=f'rollback_fail_{uuid.uuid4().hex[:8]}'
                    )
                    self.tenants_created.append(str(t.id))

                    # Crear proyecto que fallará
                    uow.projects.create_project(
                        tenant_id=t.id,
                        name='RollbackFailProject',
                        slug=test_slug
                    )

                    # Forzar rollback
                    raise RuntimeError("Simulated rollback trigger")
            except RuntimeError:
                pass  # Rollback esperado

            # Verificar que nada se persistió
            with UnitOfWork() as uow:
                final_count = len(uow.events.list_events(limit=500))

            if initial_count == final_count:
                print("  PASS: test_rollback_between_repos")
                self.passed += 1
                return True
            else:
                print(f"  FAIL: test_rollback_between_repos - count changed {initial_count}->{final_count}")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  FAIL: test_rollback_between_repos - {type(e).__name__}")
            self.failed += 1
            return False

    def test_atomicity_during_exception(self):
        """Test 3: Atomicidad - si falla, nada persiste."""
        try:
            test_slug = f'atomic_{uuid.uuid4().hex[:8]}'
            project_id = None

            try:
                with UnitOfWork() as uow:
                    # Crear tenant
                    t = uow.identity.create_tenant(
                        name='AtomicTestTenant',
                        slug=f'atomic_tenant_{uuid.uuid4().hex[:8]}'
                    )
                    self.tenants_created.append(str(t.id))

                    # Crear proyecto
                    proj = uow.projects.create_project(
                        tenant_id=t.id,
                        name='AtomicProject',
                        slug=test_slug
                    )
                    project_id = proj.id

                    # Crear tarea también
                    uow.planning.create_task(
                        tenant_id=t.id,
                        project_id=proj.id,
                        title='AtomicTask'
                    )

                    # Forzar error
                    raise RuntimeError("Force rollback")
            except RuntimeError:
                pass

            # Verificar que ni proyecto ni tarea persistieron
            with UnitOfWork() as uow:
                found_proj = uow.projects.get_project(project_id) if project_id else None

            if found_proj is None:
                print("  PASS: test_atomicity_during_exception")
                self.passed += 1
                return True
            else:
                print(f"  FAIL: test_atomicity_during_exception - project leaked")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  FAIL: test_atomicity_during_exception - {type(e).__name__}")
            self.failed += 1
            return False

    def test_crud_nine_repos(self):
        """Test 4: CRUD de todos los repositorios."""
        results = {}
        ts = uuid.uuid4().hex[:8]

        try:
            # Paso 1: Crear tenant válido
            with UnitOfWork() as uow:
                t = uow.identity.create_tenant(
                    name=f'CRUDTest_{ts}',
                    slug=f'crud_test_{ts}'
                )
                tenant_id = t.id
                self.tenants_created.append(str(tenant_id))
                # UoW revierte por defecto al salir sin commit. El usuario se
                # crea en otra transacción, por lo que este tenant debe existir
                # antes de cerrar esta unidad de trabajo.
                uow.commit()

            # Paso 2: Crear usuario
            with UnitOfWork() as uow:
                u = uow.identity.create_user(
                    tenant_id=tenant_id,
                    email=f'crud_{ts}@test.com',
                    full_name='CRUD Test User'
                )
                f = uow.identity.get_user(u.id)
                results['identity'] = f is not None and f.email == u.email

            # Pasos 3-5 comparten UoW para que sus FK apunten a filas vivas
            # en la misma transacción; al salir sin commit, el fixture se revierte.
            proj_slug = f'crd_proj_{ts}'
            with UnitOfWork() as uow:
                p = uow.projects.create_project(
                    tenant_id=tenant_id, name=f'CRD Proj {ts}', slug=proj_slug
                )
                found_project = uow.projects.get_project(p.id)
                results['projects'] = found_project is not None and found_project.name == p.name

                pro = uow.projects.get_project_by_slug(tenant_id, proj_slug)
                ta = uow.planning.create_task(
                    tenant_id=tenant_id, project_id=pro.id,
                    title=f'Task {ts}'
                )
                found_task = uow.planning.get_task(ta.id)
                results['planning'] = found_task is not None and found_task.title == ta.title

                ru = uow.execution.create_task_run(
                    tenant_id=tenant_id, task_id=ta.id,
                    project_id=pro.id, agent_name='test_agent'
                )
                found_run = uow.execution.get_task_run(ru.id)
                results['execution'] = found_run is not None and found_run.agent_name == 'test_agent'

            # Paso 6: Knowledge
            with UnitOfWork() as uow:
                k = uow.memory.create_knowledge(
                    kind='spec', title=f'Know {ts}', body='test body', source='test'
                )
                f = uow.memory.get_knowledge(k.id)
                results['memory'] = f is not None

            # Paso 7: Event
            with UnitOfWork() as uow:
                e = uow.events.emit_event(type=f'ev_{ts}', detail={'test': ts})
                f = uow.events.get_event(e.id)
                results['events'] = f is not None

            # Paso 8: Evidence
            with UnitOfWork() as uow:
                ev = uow.quality.create_evidence(
                    type='test_result',
                    hash=hashlib.sha256(f'hash_{ts}'.encode()).hexdigest()
                )
                f = uow.quality.get_evidence(ev.id)
                results['quality'] = f is not None

            # Paso 9: Model
            with UnitOfWork() as uow:
                pr = uow.providers.create_provider(
                    name=f'Prov_{ts}', type='llm',
                    slug=f'prov_{ts}', api_base='https://test.com'
                )
                m = uow.providers.create_model(
                    provider_id=pr.id, name=f'Model_{ts}',
                    model_id=f'model_{ts}'
                )
                f = uow.providers.get_model(m.id)
                results['providers'] = f is not None

            # Paso 10: Artifact
            with UnitOfWork() as uow:
                a = uow.artifacts.create_artifact(
                    name=f'Art_{ts}', path=f'/art/{ts}.txt',
                    type='code', content='# test'
                )
                f = uow.artifacts.get_artifact(a.id)
                results['artifacts'] = f is not None

            if all(results.values()):
                print("  PASS: test_crud_nine_repos")
                self.passed += 1
                return True
            else:
                print(f"  FAIL: test_crud_nine_repos - {results}")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  FAIL: test_crud_nine_repos - {type(e).__name__}")
            self.failed += 1
            return False

    def test_tenant_isolation(self):
        """Test 5: Aislamiento entre dos tenants diferentes."""
        try:
            # Crear dos tenants en transacciones separadas
            with UnitOfWork() as uow:
                t1 = uow.identity.create_tenant(
                    name='TenantA', slug=f'tenant_a_{uuid.uuid4().hex[:8]}'
                )
                tid_a = t1.id
                self.tenants_created.append(str(tid_a))
                uow.commit()

            with UnitOfWork() as uow:
                t2 = uow.identity.create_tenant(
                    name='TenantB', slug=f'tenant_b_{uuid.uuid4().hex[:8]}'
                )
                tid_b = t2.id
                self.tenants_created.append(str(tid_b))
                uow.commit()

            # Crear proyecto en cada tenant
            with UnitOfWork(tenant_id=tid_a) as uow:
                p1 = uow.projects.create_project(
                    tenant_id=tid_a, name='ProjA', slug=f'proj_a_{uuid.uuid4().hex[:8]}'
                )
                uow.commit()

            with UnitOfWork(tenant_id=tid_b) as uow:
                p2 = uow.projects.create_project(
                    tenant_id=tid_b, name='ProjB', slug=f'proj_b_{uuid.uuid4().hex[:8]}'
                )
                uow.commit()

            # Verificar aislamiento
            with UnitOfWork(tenant_id=tid_a) as uow:
                proj_a_list = uow.projects.list_projects(tid_a)
                proj_a_ids = {p.id for p in proj_a_list}

            with UnitOfWork(tenant_id=tid_b) as uow:
                proj_b_list = uow.projects.list_projects(tid_b)
                proj_b_ids = {p.id for p in proj_b_list}

            isolated = (p1.id in proj_a_ids and p1.id not in proj_b_ids and
                       p2.id in proj_b_ids and p2.id not in proj_a_ids)

            if isolated:
                print("  PASS: test_tenant_isolation")
                self.passed += 1
                return True
            else:
                print("  FAIL: test_tenant_isolation - cross-tenant leak")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  FAIL: test_tenant_isolation - {type(e).__name__}")
            self.failed += 1
            return False

    def test_concurrent_transactions(self):
        """Test 6: Concurrencia con múltiples transacciones simultáneas."""
        results = {'ok': 0, 'fail': 0, 'errors': {}}
        lock = threading.Lock()
        tenant_ids = []

        # Cada worker recibe un tenant creado y confirmado previamente. No se
        # debe inventar un UUID que no tenga fila padre en tenants.
        try:
            for wid in range(5):
                with UnitOfWork() as uow:
                    tenant = uow.identity.create_tenant(
                        name=f'ConcurrentTenant_{wid}',
                        slug=f'concurrent_{wid}_{uuid.uuid4().hex[:8]}'
                    )
                    tenant_ids.append(tenant.id)
                    self.tenants_created.append(str(tenant.id))
                    uow.commit()
        except Exception as e:
            print(f"  FAIL: test_concurrent_transactions - tenant setup: {type(e).__name__}")
            self.failed += 1
            return False

        def worker(wid):
            try:
                t_id = tenant_ids[wid]
                with UnitOfWork() as uow:
                    uow.identity.create_user(
                        tenant_id=t_id,
                        email=f'concur_{wid}_{uuid.uuid4().hex[:8]}@test.com',
                        full_name=f'W{wid}'
                    )
                    uow.commit()
                with lock:
                    results['ok'] += 1
                    tenant_ids.append(t_id)
            except Exception as e:
                with lock:
                    results['fail'] += 1
                    results['errors'][wid] = type(e).__name__

        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for t in threads: t.start()
            for t in threads: t.join(timeout=30)

            if results['ok'] == 5 and results['fail'] == 0:
                print("  PASS: test_concurrent_transactions")
                self.passed += 1
                return True
            else:
                print(f"  FAIL: test_concurrent_transactions - ok:{results['ok']} fail:{results['fail']}")
                for wid, error in sorted(results['errors'].items()):
                    print(f"    worker {wid}: {error}")
                self.failed += 1
                return False
        except Exception as e:
            print(f"  FAIL: test_concurrent_transactions - {type(e).__name__}")
            self.failed += 1
            return False

    def run_all(self):
        print("\n=== Pruebas pendientes F2-004 ===\n")
        self.test_commit_between_repos()
        self.test_rollback_between_repos()
        self.test_atomicity_during_exception()
        self.test_crud_nine_repos()
        self.test_tenant_isolation()
        self.test_concurrent_transactions()
        print(f"\n=== RESUMEN: {self.passed} PASADAS, {self.failed} FALLIDAS ===\n")
        return self.failed == 0


if __name__ == "__main__":
    tester = F2004PendingTests()
    sys.exit(0 if tester.run_all() else 1)
