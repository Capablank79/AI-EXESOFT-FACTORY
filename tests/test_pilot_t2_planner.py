"""Requirements planner tests use an in-memory Brain and a mocked Gateway."""
import json
from uuid import UUID

import pytest

from engine.model_gateway import ModelGatewayError, ModelResponse
from engine.odd import FeatureDocument
from engine.orchestrator import (
    Orchestrator, PlanConflictError, PlanGatewayError, PlanValidationError,
    validate_requirements_plan,
)
from engine.planning_contract import SCHEMA_VERSION, build_requirement_catalog

PROJECT_ID = '7b715a0d-43ac-4c7a-88c6-6ee8af5786df'
TENANT_ID = 'acdd5c3b-0f31-4a59-8e45-22bb7a217343'
REQUIREMENTS = (
    'Users can create a profile.',
    'The app displays saved profiles.',
)


def criterion(description='Creating one profile persists exactly one profile record.', *,
              target='database.profile_count', operator='equals', value=1,
              method='integration_test', action='query', step_target='profiles for created project',
              step_input=None):
    return {
        'description': description,
        'verification': {
            'method': method,
            'steps': [{
                'action': action, 'target': step_target,
                'input': step_input if step_input is not None else {'project_id': 'current'},
            }],
        },
        'expected_result': {'target': target, 'operator': operator, 'value': value},
    }


def valid_plan():
    catalog = build_requirement_catalog(REQUIREMENTS)
    return {
        'schema_version': SCHEMA_VERSION,
        'specs': [{
            'ref': 'profile_spec', 'title': 'Profile basics',
            'description': 'Create and display stored profiles.',
            'acceptance': [
                criterion(),
                criterion('The profile list displays every saved profile.',
                          target='GET /profiles.body.items', operator='count_equals', value=1,
                          method='api_test', action='invoke', step_target='GET /profiles',
                          step_input={'profile_id': 'created_profile_id'}),
            ],
        }],
        'tasks': [
            {'ref': 'create_profile', 'spec_ref': 'profile_spec', 'title': 'Create profiles',
             'description': 'Implement profile creation and persistence.',
             'agent': 'developer', 'depends_on': []},
            {'ref': 'display_profiles', 'spec_ref': 'profile_spec', 'title': 'Display profiles',
             'description': 'Render saved profiles in the profile list.',
             'agent': 'developer', 'depends_on': ['create_profile']},
        ],
        'coverage': [
            {'requirement_id': catalog[0]['id'], 'spec_ref': 'profile_spec'},
            {'requirement_id': catalog[1]['id'], 'spec_ref': 'profile_spec'},
        ],
    }


def contract_plan_for(requirements):
    catalog = build_requirement_catalog(requirements)
    specs, tasks, coverage = [], [], []
    for index, requirement in enumerate(catalog):
        spec_ref, task_ref = f'spec_local_{index}', f'task_local_{index}'
        specs.append({
            'ref': spec_ref, 'title': f'Capability {index + 1}',
            'description': f'Implement requirement {index + 1}.',
            'acceptance': [criterion(
                f'Requirement {index + 1} produces its defined result.',
                target=f'project.requirement_{index + 1}', operator='equals', value=True,
                method='integration_test', action='execute',
                step_target=f'test requirement {index + 1}',
                step_input={'requirement_id': catalog[index]['id']},
            )],
        })
        tasks.append({
            'ref': task_ref, 'spec_ref': spec_ref,
            'title': f'Implement requirement {index + 1}',
            'description': requirement['text'], 'agent': 'developer',
            'depends_on': [f'task_local_{index - 1}'] if index else [],
        })
        coverage.append({'requirement_id': catalog[index]['id'], 'spec_ref': spec_ref})
    return {'schema_version': SCHEMA_VERSION, 'specs': specs,
            'tasks': tasks, 'coverage': coverage}


class MemoryBrain:
    tenant_id = TENANT_ID

    def __init__(self):
        self.project = {
            'id': PROJECT_ID, 'tenant_id': TENANT_ID,
            'name': 'Synthetic profile app',
            'request': {'description': '\n'.join(REQUIREMENTS)},
        }
        self.feature = FeatureDocument(PROJECT_ID, self.project['name'],
                                       self.project['request']['description']).to_json()
        self.write_calls = []
        self.rows = []
        self.links = []
        self.status = 'created'

    def get_project(self, project_id):
        return self.project if str(project_id) == PROJECT_ID else None

    def tasks(self, project_id):
        return list(self.rows)

    def get_odd_document(self, project_id):
        return self.feature

    def odd_snapshots(self, project_id):
        return []

    def persist_odd_document(self, project_id, title, body):
        self.write_calls.append(('odd', title))
        self.feature = body
        return True

    def add_task(self, task_id, project_id, title, agent, depends_on=(),
                 max_attempts=3, description=None):
        self.write_calls.append(('task', title))
        self.rows.append({
            'id': str(task_id), 'project_id': str(project_id), 'title': title,
            'description': description, 'agent': agent, 'status': 'pending',
            'depends_on': json.dumps(list(depends_on)), 'attempts': 0,
            'max_attempts': max_attempts,
        })

    def link_odd_task(self, project_id, odd_task_id, brain_task_id):
        self.write_calls.append(('link', odd_task_id))
        self.links.append((odd_task_id, str(brain_task_id)))

    def set_project_status(self, project_id, status):
        self.write_calls.append(('status', status))
        self.status = status

    def brain_task_for_odd(self, project_id, odd_task_id):
        return next((brain_id for odd_id, brain_id in self.links if odd_id == odd_task_id), None)

    def odd_task_for_brain(self, project_id, brain_task_id):
        return next((odd_id for odd_id, brain_id in self.links if brain_id == str(brain_task_id)), None)

    def persist_validated_plan(self, project_id, plan, requirements, expected_request,
                               *, replan_authorized_hash=None):
        if self.rows:
            raise PlanConflictError('Existing project plan')
        self.write_calls.append(('atomic_plan', project_id))
        spec_ids = {}
        feature = FeatureDocument(PROJECT_ID, self.project['name'], json.dumps(expected_request))
        for spec in plan['specs']:
            stored = feature.add_spec(spec['title'], spec['description'], spec['acceptance'])
            spec_ids[spec['id']] = stored.id
        task_ids = {task['id']: str(UUID(int=index + 1))
                    for index, task in enumerate(plan['tasks'])}
        for task in plan['tasks']:
            odd_task = feature.add_task(spec_ids[task['spec_id']], task['agent'], task['description'])
            self.add_task(task_ids[task['id']], project_id, task['title'], task['agent'],
                          [task_ids[item] for item in task['depends_on']],
                          description=task['description'])
            self.link_odd_task(project_id, odd_task.id, task_ids[task['id']])
        self.feature = feature.to_json()
        self.set_project_status(project_id, 'planned')
        return {'schema_version': plan['schema_version'],
                'plan_version': 1, 'candidate_hash': 'a' * 64,
                'plan_hash': 'b' * 64, 'idempotent': False,
                'specs': plan['specs'], 'requirement_coverage': plan['requirement_coverage'],
                'tasks': self.tasks(project_id)}


class MockGateway:
    def __init__(self, plan=None, error=None, plans=None):
        self.plans = list(plans) if plans is not None else [plan]
        self.error = error
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        return ModelResponse(content=json.dumps(plan), structured=plan)


def orchestrator(gateway=None):
    brain = MemoryBrain()
    return Orchestrator('.', tenant_id=TENANT_ID, brain=brain, backend='sqlite'), brain


def diagnostic_codes(error):
    return {item['code'] for item in error.diagnostics}


def test_contract_aggregates_invalid_ids_edges_agent_criteria_and_dependencies_before_writes():
    plan = valid_plan()
    plan['coverage'][0]['requirement_id'] = 'REQ-' + 'f' * 64
    plan['coverage'][1] = dict(plan['coverage'][0])
    plan['tasks'][0]['agent'] = 'root'
    plan['tasks'][1]['depends_on'] = ['missing_task']
    plan['specs'][0]['acceptance'][0]['verification']['steps'] = ['check that it works']
    app, brain = orchestrator()
    gateway = MockGateway(plan)
    with pytest.raises(PlanValidationError) as raised:
        app.plan_from_requirements(PROJECT_ID, gateway=gateway)
    codes = diagnostic_codes(raised.value)
    assert {'unknown_requirement_id', 'duplicate_coverage_edge', 'requirement_not_covered',
            'unsupported_agent', 'unknown_task_ref', 'expected_action_target_input'} <= codes
    assert 'missing_task' not in str(raised.value) and 'root' not in str(raised.value)
    assert len(gateway.requests) == 2
    assert brain.write_calls == [] and brain.rows == []


def test_coverage_many_to_many_maps_local_refs_and_rejects_duplicate_edges():
    plan = valid_plan()
    catalog = build_requirement_catalog(REQUIREMENTS)
    plan['specs'].append({
        'ref': 'profile_view', 'title': 'Profile view', 'description': 'Display stored profiles.',
        'acceptance': [criterion('The profile list displays every saved profile.')],
    })
    plan['tasks'].append({
        'ref': 'verify_view', 'spec_ref': 'profile_view', 'title': 'Verify profile view',
        'description': 'Test displaying stored profiles.', 'agent': 'qa',
        'depends_on': ['display_profiles'],
    })
    plan['coverage'].append({'requirement_id': catalog[0]['id'], 'spec_ref': 'profile_view'})
    mapped = validate_requirements_plan(plan, catalog)
    assert [item['id'] for item in mapped['specs']] == ['S001', 'S002']
    assert [item['id'] for item in mapped['tasks']] == ['T001', 'T002', 'T003']
    assert mapped['requirement_coverage'][0]['spec_ids'] == ['S001', 'S002']
    assert mapped['requirement_coverage'][1]['spec_ids'] == ['S001']

    plan['coverage'].append(dict(plan['coverage'][0]))
    with pytest.raises(PlanValidationError) as raised:
        validate_requirements_plan(plan, catalog)
    assert 'duplicate_coverage_edge' in diagnostic_codes(raised.value)


@pytest.mark.parametrize('mutation,expected', [
    (lambda plan: plan['coverage'].pop(), 'requirement_not_covered'),
    (lambda plan: plan['coverage'][0].update(requirement_id='unknown'), 'unknown_requirement_id'),
    (lambda plan: plan['tasks'][0].update(spec_ref='unknown_spec'), 'unknown_spec_ref'),
    (lambda plan: plan['tasks'][0].update(depends_on=['unknown_task']), 'unknown_task_ref'),
    (lambda plan: plan['tasks'][0].update(depends_on=['create_profile']), 'self_dependency'),
    (lambda plan: plan['tasks'][0].update(agent='root'), 'unsupported_agent'),
    (lambda plan: plan.update(schema_version='v0'),
     'unsupported_version'),
])
def test_contract_rejects_invalid_edges_refs_dependencies_and_agents(mutation, expected):
    plan = valid_plan()
    mutation(plan)
    with pytest.raises(PlanValidationError) as raised:
        validate_requirements_plan(plan, REQUIREMENTS)
    assert expected in diagnostic_codes(raised.value)


def test_contract_rejects_cycles_and_legacy_free_form_criteria():
    plan = valid_plan()
    plan['tasks'][0]['depends_on'] = ['display_profiles']
    with pytest.raises(PlanValidationError) as raised:
        validate_requirements_plan(plan, REQUIREMENTS)
    assert 'dependency_cycle' in diagnostic_codes(raised.value)
    plan = valid_plan()
    plan['specs'][0]['acceptance'] = ['Historical free-form criterion']
    with pytest.raises(PlanValidationError) as raised:
        validate_requirements_plan(plan, REQUIREMENTS)
    assert 'expected_structured_object' in diagnostic_codes(raised.value)


@pytest.mark.parametrize('mutate,code', [
    (lambda plan: plan['specs'][0].update(acceptance=[]), 'invalid_array_size'),
    (lambda plan: plan['specs'][0].update(acceptance=[criterion()] * 21), 'invalid_array_size'),
    (lambda plan: plan['specs'][0]['acceptance'][0].pop('expected_result'),
     'missing_or_unexpected_fields'),
    (lambda plan: plan['specs'][0]['acceptance'][0]['verification'].update(method='guess'),
     'unsupported_method'),
    (lambda plan: plan['specs'][0]['acceptance'][0]['verification'].update(steps=[]),
     'invalid_array_size'),
    (lambda plan: plan['specs'][0]['acceptance'][0]['verification']['steps'][0].update(
        action='guess'), 'unsupported_action'),
    (lambda plan: plan['specs'][0]['acceptance'][0]['expected_result'].update(value=None),
     'invalid_value'),
])
def test_malformed_structured_acceptance_rejected_before_persistence(mutate, code):
    plan = valid_plan()
    mutate(plan)
    app, brain = orchestrator()
    gateway = MockGateway(plans=[plan, plan])
    with pytest.raises(PlanValidationError) as raised:
        app.plan_from_requirements(PROJECT_ID, gateway=gateway)
    assert code in diagnostic_codes(raised.value)
    assert len(gateway.requests) == 2
    assert brain.write_calls == [] and brain.rows == []


def test_one_bounded_repair_succeeds_without_sending_first_draft_or_persisting_before_validation():
    first = valid_plan()
    first['coverage'][0]['requirement_id'] = 'invalid'
    repaired = valid_plan()
    app, brain = orchestrator()
    gateway = MockGateway(plans=[first, repaired])
    result = app.plan_from_requirements(PROJECT_ID, gateway=gateway)
    assert len(gateway.requests) == 2
    repair_prompt = gateway.requests[1].messages[1]['content']
    assert 'Validation diagnostics' in repair_prompt
    assert 'invalid' not in repair_prompt
    assert result['schema_version'] == SCHEMA_VERSION
    assert len(brain.rows) == 2 and brain.status == 'planned'


def test_one_bounded_repair_failure_fails_closed_without_persistence():
    invalid = valid_plan()
    invalid['coverage'] = []
    app, brain = orchestrator()
    gateway = MockGateway(plans=[invalid, invalid])
    with pytest.raises(PlanValidationError):
        app.plan_from_requirements(PROJECT_ID, gateway=gateway)
    assert len(gateway.requests) == 2
    assert brain.write_calls == [] and brain.rows == []


def test_planner_prompt_uses_single_versioned_schema_and_authoritative_requirement_catalog():
    app, _ = orchestrator()
    gateway = MockGateway(valid_plan())
    app.plan_from_requirements(PROJECT_ID, gateway=gateway)
    prompt = gateway.requests[0].messages[1]['content']
    catalog = build_requirement_catalog(REQUIREMENTS)
    assert SCHEMA_VERSION in prompt
    assert json.dumps(catalog, ensure_ascii=False, sort_keys=True) in prompt
    assert 'Factory resolves refs to' in prompt
    assert 'requirement_id/spec_ref edges' in prompt


@pytest.mark.parametrize(('project_kind', 'requirements'), [
    ('crud', ['Users create products.', 'Users update product quantities.']),
    ('document_processing', [
        'The system extracts text from uploaded documents.',
        'The system reports validation failures for unreadable documents.',
    ]),
    ('api', ['The API creates resources with POST.', 'The API returns 404 for missing resources.']),
])
def test_mocked_end_to_end_plan_contract_for_project_types(project_kind, requirements):
    app, brain = orchestrator()
    brain.project['request'] = {'requirements': requirements, 'project_kind': project_kind}
    gateway = MockGateway(contract_plan_for(requirements))

    result = app.plan_from_requirements(PROJECT_ID, gateway=gateway)

    expected_catalog = build_requirement_catalog(requirements)
    assert result['schema_version'] == SCHEMA_VERSION
    assert len(result['specs']) == len(requirements)
    assert len(result['tasks']) == len(requirements)
    assert [edge['requirement_id'] for edge in result['requirement_coverage']] == [
        item['id'] for item in expected_catalog
    ]
    assert brain.status == 'planned'
    assert len(brain.rows) == len(requirements)
    assert all(isinstance(ac, dict)
               for spec in FeatureDocument.from_json(brain.feature).specs
               for ac in spec.acceptance)


@pytest.mark.parametrize('error', [TimeoutError(), ModelGatewayError('safe test timeout', retryable=True)])
def test_gateway_timeout_or_failure_does_not_write_plan(error):
    app, brain = orchestrator()
    with pytest.raises(PlanGatewayError):
        app.plan_from_requirements(PROJECT_ID, gateway=MockGateway(error=error))
    assert brain.write_calls == []


def test_non_json_gateway_content_is_rejected_before_writes():
    class BadJsonGateway:
        def complete(self, request):
            return ModelResponse(content='not JSON', structured=None)

    app, brain = orchestrator()
    with pytest.raises(PlanValidationError) as raised:
        app.plan_from_requirements(PROJECT_ID, gateway=BadJsonGateway())
    assert 'malformed_json' in diagnostic_codes(raised.value)
    assert brain.write_calls == []


def test_json_content_parser_preserves_structured_acceptance_round_trip():
    class ContentOnlyGateway:
        def complete(self, request):
            return ModelResponse(content=json.dumps(valid_plan()), structured=None)

    app, brain = orchestrator()
    app.plan_from_requirements(PROJECT_ID, gateway=ContentOnlyGateway())
    restored = FeatureDocument.from_json(brain.feature)
    assert restored.specs[0].acceptance == valid_plan()['specs'][0]['acceptance']


def test_valid_requirements_plan_uses_gateway_persists_odd_and_stays_unapproved():
    app, brain = orchestrator()
    gateway = MockGateway(valid_plan())
    result = app.plan_from_requirements(PROJECT_ID, gateway=gateway)

    request = gateway.requests[0]
    assert request.response_format == 'json'
    assert request.required_capabilities == frozenset({'text', 'json'})
    assert request.metadata['tenant_id'] == TENANT_ID
    assert request.metadata['project_id'] == PROJECT_ID
    assert len(result['specs']) == 1 and len(result['tasks']) == 2
    assert brain.status == 'planned'
    assert all(task['status'] == 'pending' for task in result['tasks'])
    assert all(UUID(task['id']) for task in result['tasks'])
    assert len(brain.links) == 2
    assert not any(event[0] == 'approval' for event in brain.write_calls)


def test_existing_plan_conflict_rejects_without_persistence():
    app, brain = orchestrator()
    brain.rows.append({'id': 'already-planned'})
    gateway = MockGateway(valid_plan())
    with pytest.raises(PlanConflictError):
        app.plan_from_requirements(PROJECT_ID, gateway=gateway)
    assert len(gateway.requests) == 1
    assert brain.write_calls == []


def test_coverage_validation_accepts_complete_valid_reference_map():
    validated = validate_requirements_plan(valid_plan(), REQUIREMENTS)
    assert [edge['requirement_id'] for edge in validated['requirement_coverage']] == [
        item['id'] for item in build_requirement_catalog(REQUIREMENTS)
    ]


def test_legacy_string_acceptance_round_trips_without_becoming_a_valid_new_plan():
    feature = FeatureDocument(PROJECT_ID, 'Legacy', 'Historical request')
    feature.add_spec('Legacy spec', 'Historical description', ['Historical criterion text'])
    restored = FeatureDocument.from_json(feature.to_json())
    assert restored.specs[0].acceptance == ['Historical criterion text']
    assert '- AC1: Historical criterion text' in restored.specs[0].to_markdown()

    plan = valid_plan()
    plan['specs'][0]['acceptance'] = ['Historical criterion text']
    with pytest.raises(PlanValidationError, match='expected_structured_object'):
        validate_requirements_plan(plan, REQUIREMENTS)


def test_structured_acceptance_reaches_agent_execution_prompt(tmp_path):
    from engine.agents import make_prompt
    from engine.agent_runtime import _AgentRuntimeDispatch
    from engine.odd import ODDEngine

    brain = MemoryBrain()
    feature = FeatureDocument(PROJECT_ID, 'Synthetic profile app', 'request')
    saved_spec = feature.add_spec('Profiles', 'Profile behavior', [criterion()])
    odd_task = feature.add_task(saved_spec.id, 'developer', 'Create profile behavior')
    brain.feature = feature.to_json()
    brain.link_odd_task(PROJECT_ID, odd_task.id, 'brain-task-1')

    received = {}

    class Adapter:
        def execute(self, task, project, context):
            received.update(context)

    runtime = type('DispatchHarness', (_AgentRuntimeDispatch,), {})()
    runtime.root = tmp_path
    runtime.adapters = {'developer': Adapter()}
    runtime.odd = ODDEngine(brain)
    runtime.brain = brain
    runtime._dispatch(
        {'agent': 'developer', 'title': 'Create profiles', 'id': 'brain-task-1'},
        {'id': PROJECT_ID, 'name': 'Synthetic profile app',
         'request': 'request', 'tenant_id': TENANT_ID},
    )
    assert received['acceptance_criteria'] == [criterion()]
    prompt = make_prompt(
        {'agent': 'developer', 'title': 'Create profiles'},
        {'name': 'Synthetic profile app', 'request': 'request'}, received,
    )
    for field in ('description', 'verification', 'expected_result', 'database.profile_count'):
        assert field in prompt


def test_api_plan_route_uses_requirements_method_by_default(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import api.app as api_module

    calls = []
    orchestrator = SimpleNamespace(
        plan_from_requirements=lambda project_id: calls.append(('requirements', project_id)) or {'tasks': []},
        plan_skeleton=lambda project_id: calls.append(('legacy', project_id)) or [],
    )
    principal = SimpleNamespace(tenant_id=TENANT_ID)
    runtime = SimpleNamespace(orchestrator=orchestrator)
    monkeypatch.setattr(api_module, '_runtime', lambda request, current: runtime)
    monkeypatch.setattr(api_module, '_project_or_404', lambda runtime, project_id: (UUID(PROJECT_ID), {}))
    monkeypatch.setattr(api_module, '_require', lambda principal, roles: None)
    request = SimpleNamespace(
        state=SimpleNamespace(principal=principal),
        path_params={'project_id': PROJECT_ID},
        query_params={},
    )
    response = asyncio.run(api_module.plan_project(request))
    assert response.status_code == 201
    assert calls == [('requirements', PROJECT_ID)]


def test_api_plan_route_forwards_explicit_replan_fingerprint(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import api.app as api_module

    fingerprint = 'a' * 64
    calls = []
    orchestrator = SimpleNamespace(
        plan_from_requirements=lambda project_id, **kwargs: (
            calls.append((project_id, kwargs)) or {'tasks': []}
        ),
        plan_skeleton=lambda project_id: [],
    )
    principal = SimpleNamespace(tenant_id=TENANT_ID)
    runtime = SimpleNamespace(orchestrator=orchestrator)
    monkeypatch.setattr(api_module, '_runtime', lambda request, current: runtime)
    monkeypatch.setattr(api_module, '_project_or_404', lambda runtime, project_id: (UUID(PROJECT_ID), {}))
    monkeypatch.setattr(api_module, '_require', lambda principal, roles: None)
    request = SimpleNamespace(
        state=SimpleNamespace(principal=principal),
        path_params={'project_id': PROJECT_ID},
        query_params={'replan_authorized_hash': fingerprint},
    )
    response = asyncio.run(api_module.plan_project(request))
    assert response.status_code == 201
    assert calls == [(PROJECT_ID, {'replan_authorized_hash': fingerprint})]


def test_cli_plan_defaults_to_requirements_and_keeps_explicit_legacy_path(monkeypatch, tmp_path):
    import factory

    calls = []

    class FakeRuntime:
        tenant_id = TENANT_ID

        class Brain:
            @staticmethod
            def get_project(project_id):
                return {'id': project_id}

        brain = Brain()

        class Planner:
            def plan_from_requirements(self, project_id, **kwargs):
                calls.append(('requirements', project_id, kwargs))
                return {'tasks': []}

            def plan_skeleton(self, project_id):
                calls.append(('legacy', project_id))
                return []

        orchestrator = Planner()

        def __init__(self, root, tenant_id=None):
            pass

    monkeypatch.setattr(factory, ''.join(('Postgres', 'Runtime')), FakeRuntime)
    code = factory.main(['--root', str(tmp_path), '--tenant-id', TENANT_ID,
                         'plan', PROJECT_ID])
    assert code == 0 and calls.pop() == ('requirements', PROJECT_ID, {})
    fingerprint = 'a' * 64
    code = factory.main(['--root', str(tmp_path), '--tenant-id', TENANT_ID,
                         'plan', PROJECT_ID, '--replan-authorized-hash', fingerprint])
    assert code == 0 and calls.pop() == (
        'requirements', PROJECT_ID, {'replan_authorized_hash': fingerprint},
    )
    code = factory.main(['--root', str(tmp_path), '--tenant-id', TENANT_ID,
                         'plan', PROJECT_ID, '--legacy-skeleton'])
    assert code == 0 and calls.pop() == ('legacy', PROJECT_ID)
