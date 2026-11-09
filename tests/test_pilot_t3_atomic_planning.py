"""PostgreSQL integration tests for atomic, serialized plan persistence."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from brain.postgres_store import Brain as PostgresBrain
from engine.model_gateway import ModelResponse
from engine.orchestrator import Orchestrator
from engine.planning_contract import SCHEMA_VERSION, build_requirement_catalog
from infrastructure.plan_approval import approval_status, record_plan_decision
from infrastructure.uow import UnitOfWork

TENANT_ID = UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


def plan_for(requirement='Users can create a profile.', title_suffix=''):
    requirement_id = build_requirement_catalog([requirement])[0]['id']
    return {
        'schema_version': SCHEMA_VERSION,
        'specs': [{
            'ref': 'profile_spec', 'title': 'Profile feature', 'description': 'Manage profiles.',
            'acceptance': [{
                'description': 'Creating one profile persists a profile record.',
                'verification': {
                    'method': 'integration_test',
                    'steps': [{
                        'action': 'query', 'target': 'profiles for created project',
                        'input': {'project_id': 'current'},
                    }],
                },
                'expected_result': {
                    'target': 'database.profile_count', 'operator': 'equals', 'value': 1,
                },
            }],
        }],
        'tasks': [{
            'ref': 'create_profiles', 'spec_ref': 'profile_spec',
            'title': 'Create profiles' + title_suffix,
            'description': 'Implement profile creation and persistence.',
            'agent': 'developer', 'depends_on': [],
        }],
        'coverage': [{'requirement_id': requirement_id, 'spec_ref': 'profile_spec'}],
    }


def plan_for_requirements(requirements):
    catalog = build_requirement_catalog(requirements)
    specs, tasks, coverage = [], [], []
    for index, requirement in enumerate(catalog):
        spec_ref, task_ref = f'spec_{index}', f'task_{index}'
        specs.append({
            'ref': spec_ref, 'title': f'Capability {index + 1}',
            'description': f'Implement project requirement {index + 1}.',
            'acceptance': [{
                'description': f'Requirement {index + 1} has a verifiable outcome.',
                'verification': {
                    'method': 'integration_test',
                    'steps': [{
                        'action': 'execute', 'target': f'test requirement {index + 1}',
                        'input': {'requirement_id': catalog[index]['id']},
                    }],
                },
                'expected_result': {
                    'target': f'requirement_{index + 1}.result',
                    'operator': 'equals', 'value': True,
                },
            }],
        })
        tasks.append({
            'ref': task_ref, 'spec_ref': spec_ref,
            'title': f'Implement requirement {index + 1}',
            'description': requirement['text'], 'agent': 'developer',
            'depends_on': [f'task_{index - 1}'] if index else [],
        })
        coverage.append({'requirement_id': catalog[index]['id'], 'spec_ref': spec_ref})
    return {'schema_version': SCHEMA_VERSION, 'specs': specs,
            'tasks': tasks, 'coverage': coverage}


class MockGateway:
    def __init__(self, plan, barrier=None):
        self.plan = plan
        self.barrier = barrier
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.barrier:
            self.barrier.wait(timeout=15)
        return ModelResponse(content=json.dumps(self.plan), structured=self.plan)


def make_project(tmp_path, register_project):
    orchestrator = Orchestrator(tmp_path, tenant_id=TENANT_ID, backend='postgres')
    project_id = orchestrator.create_project(
        'T3 Synthetic ' + uuid4().hex[:8], 'Users can create a profile.',
    )
    register_project(project_id, TENANT_ID)
    return orchestrator, project_id


def read_plan_state(project_id):
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        project_spec = uow.projects.get_project_spec(UUID(project_id))
        document = uow.memory.get_odd_document(UUID(project_id))
        tasks = uow.planning.list_tasks_by_project(UUID(project_id))
        with uow._get_connection().cursor() as cursor:
            cursor.execute(
                "SELECT type, detail FROM events WHERE tenant_id = %s AND project_id = %s "
                "AND type IN ('task.created', 'project.status') ORDER BY created_at, id",
                (str(TENANT_ID), project_id),
            )
            events = cursor.fetchall()
            cursor.execute(
                "SELECT COUNT(*) FROM project_snapshots WHERE project_id = %s",
                (project_id,),
            )
            snapshot_count = cursor.fetchone()[0]
    return project_spec, document, tasks, events, snapshot_count


def test_plan_persistence_is_idempotent_without_duplicate_records(tmp_path, isolated_postgres):
    orchestrator, project_id = make_project(tmp_path, isolated_postgres)
    first = orchestrator.plan_from_requirements(project_id, gateway=MockGateway(plan_for()))
    repeat_gateway = MockGateway(plan_for())
    second = orchestrator.plan_from_requirements(project_id, gateway=repeat_gateway)

    spec, document, tasks, events, snapshots = read_plan_state(project_id)
    assert first['idempotent'] is False
    assert second['idempotent'] is True
    assert repeat_gateway.calls == 0
    assert first['candidate_hash'] == second['candidate_hash']
    assert spec.spec['version'] == 1
    expected_acceptance = plan_for()['specs'][0]['acceptance']
    assert spec.spec['plan']['specs'][0]['acceptance'] == expected_acceptance
    assert len(tasks) == 1
    assert sum(event[0] == 'task.created' for event in events) == 1
    assert snapshots == 0
    assert document and 'T001' in document.body
    from engine.odd import FeatureDocument
    restored = FeatureDocument.from_json(document.body)
    assert restored.specs[0].acceptance == expected_acceptance
    assert restored.specs[0].to_markdown().find('database.profile_count') >= 0
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        assert approval_status(uow._get_connection(), TENANT_ID, UUID(project_id))['approved'] is False


@pytest.mark.parametrize(('project_kind', 'requirements'), [
    ('crud', ['Users create products.', 'Users update product quantities.']),
    ('document_processing', [
        'The system extracts text from uploaded documents.',
        'The system reports validation failures for unreadable documents.',
    ]),
    ('api', ['The API creates resources with POST.', 'The API returns 404 for missing resources.']),
])
def test_distinct_project_types_persist_versioned_plans_without_execution(
        tmp_path, isolated_postgres, project_kind, requirements):
    orchestrator = Orchestrator(tmp_path, tenant_id=TENANT_ID, backend='postgres')
    project_id = orchestrator.create_project(
        f'T3 {project_kind} contract {uuid4().hex[:8]}', '\n'.join(requirements),
    )
    isolated_postgres(project_id, TENANT_ID)
    result = orchestrator.plan_from_requirements(
        project_id, gateway=MockGateway(plan_for_requirements(requirements)),
    )
    spec, document, tasks, _, snapshots = read_plan_state(project_id)
    catalog = build_requirement_catalog(requirements)
    assert result['schema_version'] == SCHEMA_VERSION
    assert spec.spec['plan']['schema_version'] == SCHEMA_VERSION
    assert [entry['requirement_id'] for entry in spec.spec['plan']['requirement_coverage']] == [
        item['id'] for item in catalog
    ]
    assert len(tasks) == len(requirements)
    from engine.odd import FeatureDocument
    restored = FeatureDocument.from_json(document.body)
    assert all(isinstance(criterion, dict)
               and set(criterion) == {'description', 'verification', 'expected_result'}
               for odd_spec in restored.specs for criterion in odd_spec.acceptance)
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        state = approval_status(uow._get_connection(), TENANT_ID, UUID(project_id))
        with uow._get_connection().cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM task_runs WHERE project_id = %s', (project_id,))
            run_count = cursor.fetchone()[0]
    assert state['approved'] is False
    assert run_count == 0 and snapshots == 0


def test_persistence_failure_rolls_back_every_plan_record(tmp_path, isolated_postgres, monkeypatch):
    orchestrator, project_id = make_project(tmp_path, isolated_postgres)
    before = orchestrator.brain.get_odd_document(project_id)
    from infrastructure.repositories.odd_hybrid import OddHybridRepository
    original = OddHybridRepository.link_odd_task
    calls = {'count': 0}

    def fail_after_link(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        calls['count'] += 1
        if calls['count'] == 1:
            raise RuntimeError('synthetic injected persistence failure')
        return result

    monkeypatch.setattr(OddHybridRepository, 'link_odd_task', fail_after_link)
    with pytest.raises(RuntimeError, match='synthetic injected'):
        orchestrator.plan_from_requirements(project_id, gateway=MockGateway(plan_for()))
    spec, document, tasks, events, snapshots = read_plan_state(project_id)
    assert spec is None
    assert document.body == before
    assert tasks == []
    assert not any(event[0] == 'task.created' for event in events)
    assert snapshots == 0


def test_concurrent_same_plan_serializes_to_one_persisted_version(tmp_path, isolated_postgres):
    orchestrator, project_id = make_project(tmp_path, isolated_postgres)
    barrier = threading.Barrier(2)
    plan = plan_for()

    def run_once():
        return orchestrator.plan_from_requirements(
            project_id, gateway=MockGateway(plan, barrier=barrier),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_once(), range(2)))
    spec, _, tasks, events, snapshots = read_plan_state(project_id)
    assert sorted(result['idempotent'] for result in results) == [False, True]
    assert spec.spec['version'] == 1
    assert len(tasks) == 1
    assert sum(event[0] == 'task.created' for event in events) == 1
    assert snapshots == 0


def test_replan_requires_current_hash_preserves_history_and_invalidates_approval(
        tmp_path, isolated_postgres):
    orchestrator, project_id = make_project(tmp_path, isolated_postgres)
    first = orchestrator.plan_from_requirements(project_id, gateway=MockGateway(plan_for()))
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        approved = record_plan_decision(
            uow, UUID(project_id), actor_id='synthetic-test-operator',
            expected_hash=first['plan_hash'], approve=True,
        )
    assert approved['approved'] is True

    changed = plan_for(title_suffix=' v2')
    unauthorized_gateway = MockGateway(changed)
    unchanged = orchestrator.plan_from_requirements(project_id, gateway=unauthorized_gateway)
    assert unchanged['idempotent'] is True
    assert unauthorized_gateway.calls == 0

    second = orchestrator.plan_from_requirements(
        project_id, gateway=MockGateway(changed),
        replan_authorized_hash=first['plan_hash'],
    )
    spec, document, tasks, events, snapshots = read_plan_state(project_id)
    assert second['plan_version'] == 2
    assert second['plan_hash'] != first['plan_hash']
    assert spec.spec['version'] == 2
    assert snapshots == 1
    assert sum(task.status == 'cancelled' for task in tasks) == 1
    assert sum(task.status == 'pending' for task in tasks) == 1
    assert any(event[0] == 'project.status' and event[1].get('plan_event') == 'revised'
               for event in events)
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        state = approval_status(uow._get_connection(), TENANT_ID, UUID(project_id))
        archived = uow.odd_hybrid.list_project_snapshots(UUID(project_id))
    assert state == {'approved': False, 'plan_hash': second['plan_hash']}
    assert archived[0]['snapshot']['plan_spec']['plan']['specs'][0]['acceptance'] == (
        plan_for()['specs'][0]['acceptance'])
    assert document and 'v002-T001' in document.body


def test_planning_is_tenant_scoped(tmp_path, isolated_postgres):
    orchestrator, project_id = make_project(tmp_path, isolated_postgres)
    with pytest.raises(KeyError, match='Project not found'):
        PostgresBrain(tenant_id=uuid4()).persist_validated_plan(
            project_id, plan_for(), ['Users can create a profile.'],
            {'description': 'Users can create a profile.'},
        )
    assert orchestrator.brain.tasks(project_id) == []

