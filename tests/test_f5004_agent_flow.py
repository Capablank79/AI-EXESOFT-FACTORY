"""End-to-end PostgreSQL integration for routed agents, handoffs and recovery."""
import os
import tempfile
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

from engine.agent_runtime import AgentRuntime
from engine.agents import GatewayModelAdapter
from engine.model_gateway import ModelGateway, ModelGatewayError, ModelResponse, ProviderRoute
from engine.runtime import Result
from infrastructure.repositories.events import EventsRepository
from infrastructure.repositories.memory import MemoryRepository
from quality.evidence import record_evidence
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
AGENTS = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')


class MockProvider:
    def __init__(self, name, failures=0):
        self.name = name
        self.failures = failures
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if self.failures:
            self.failures -= 1
            raise ModelGatewayError('mock transient provider failure', retryable=True)
        return ModelResponse(f'{self.name} response')


class RoutedAgent:
    def __init__(self, role, primary, fallback):
        self.role = role
        self.contexts = []
        gateway = ModelGateway(routes=[
            ProviderRoute('primary', primary, model='primary-model', priority=0,
                          capabilities=frozenset({'text', 'json'}), max_retries=0),
            ProviderRoute('fallback', fallback, model='fallback-model', priority=1,
                          capabilities=frozenset({'text', 'json'}), max_retries=0),
        ], max_retries=0)
        self.model_adapter = GatewayModelAdapter(gateway)

    def execute(self, task, project, context):
        self.contexts.append(context)
        response = self.model_adapter.execute(task, project, context)
        if not response.success:
            return response
        evidence = (f'artifact:{self.role}:verified',)
        if self.role in {'developer', 'qa'}:
            root = Path(context['workspace'])
            root.mkdir(parents=True, exist_ok=True)
            output = 'Ran 1 test'
            evidence = (record_evidence(root, {
                'schema': 'exesoft.quality.test.v1', 'id': uuid.uuid4().hex,
                'kind': 'test', 'runner': 'sandy', 'status': 'passed', 'returncode': 0,
                'sandy_exit_code': 0, 'output': output,
                'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
                'tests_run': 1, 'result_sha256': 'a' * 64, 'partial_ok': False,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }),)
        return Result(
            True, response.summary, evidence,
            approved=self.role == 'qa',
        )


def make_agents():
    providers = {}
    adapters = {}
    for role in AGENTS:
        primary_failures = 1 if role in {'pm', 'architect'} else 0
        fallback_failures = 1 if role == 'architect' else 0
        primary = MockProvider(f'{role}-primary', primary_failures)
        fallback = MockProvider(f'{role}-fallback', fallback_failures)
        providers[role] = (primary, fallback)
        adapters[role] = RoutedAgent(role, primary, fallback)
    return providers, adapters


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


def test_routed_multi_agent_flow_survives_provider_failure_and_handoff_recovery(
    workspace, monkeypatch
):
    providers, adapters = make_agents()
    runtime = AgentRuntime(workspace, adapters, tenant_id=TENANT_ID)
    project_id = runtime.orchestrator.create_project(
        f'F5-004 Integrated flow {uuid.uuid4()}',
        'Keep specialist decisions and artifacts across retries and restarts',
    )
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)
    project_uuid = uuid.UUID(project_id)

    pm_result = runtime.step(project_id)
    assert pm_result['state'] == 'done'
    assert 'primary:retryable_error -> fallback:success' in pm_result['summary']
    assert len(runtime.handoffs(project_id)) == 1

    failed_architect = runtime.step(project_id)
    assert failed_architect['state'] == 'pending'
    assert failed_architect['task'] == tasks[1]['id']
    assert failed_architect['summary'].find('primary:retryable_error') >= 0
    assert failed_architect['summary'].find('fallback:retryable_error') >= 0
    assert len(runtime.handoffs(project_id)) == 1

    architect_context = adapters['architect'].contexts[0]
    assert architect_context['handoffs'][0]['from_agent'] == 'pm'
    assert architect_context['handoffs'][0]['artifacts'] == ['artifact:pm:verified']
    assert architect_context['tenant_id'] == str(TENANT_ID)
    assert architect_context['project_id'] == project_id
    assert providers['architect'][0].calls[0].metadata['run_id'] == failed_architect['run']

    successful_architect = runtime.step(project_id)
    assert successful_architect['state'] == 'done'
    assert 'primary:success' in successful_architect['summary']
    assert len(runtime.handoffs(project_id)) == 2
    assert runtime.step(project_id)['state'] == 'done'  # UX

    original_create_handoff = MemoryRepository.create_agent_handoff

    def interrupt_developer_handoff(repository, handoff):
        if handoff.from_agent == 'developer':
            raise RuntimeError('simulated process interruption during handoff commit')
        return original_create_handoff(repository, handoff)

    monkeypatch.setattr(MemoryRepository, 'create_agent_handoff', interrupt_developer_handoff)
    with pytest.raises(RuntimeError, match='simulated process interruption'):
        runtime.step(project_id)

    assert len(runtime.handoffs(project_id)) == 3
    developer_task = next(task for task in runtime.brain.tasks(project_id)
                          if task['id'] == tasks[3]['id'])
    assert developer_task['status'] == 'running'
    monkeypatch.undo()

    restarted_agents = make_agents()[1]
    restarted = AgentRuntime(workspace, restarted_agents, tenant_id=TENANT_ID)
    assert restarted.recover(project_id) == 1
    interrupted_run = next(
        run for run in restarted.runs(project_id)
        if run['task_id'] == tasks[3]['id'] and run['status'] == 'interrupted'
    )
    assert interrupted_run['status'] == 'interrupted'
    assert len(restarted.handoffs(project_id)) == 3

    recovered_developer = restarted.step(project_id)
    assert recovered_developer['state'] == 'done'
    assert len(restarted_agents['developer'].contexts[0]['handoffs']) == 3
    assert restarted_agents['developer'].contexts[0]['handoffs'][-1]['from_agent'] == 'ux'

    remaining = restarted.run(project_id, max_steps=10)
    assert remaining[-1]['state'] == 'done'
    assert len(restarted.handoffs(project_id)) == 6
    assert all(task['status'] == 'done' for task in restarted.brain.tasks(project_id))

    events = EventsRepository(tenant_id=TENANT_ID).execution_trace(project_uuid)
    assert all(event.tenant_id == TENANT_ID and event.project_id == project_uuid for event in events)
    assert sum(event.type == 'agent.handoff' for event in events) == 6
    assert any(event.type == 'task.retry' and event.task_id == uuid.UUID(tasks[1]['id'])
               for event in events)
    handoff_ids = {handoff['handoff_id'] for handoff in restarted.handoffs(project_id)}
    event_handoff_ids = {event.detail['handoff_id'] for event in events if event.type == 'agent.handoff'}
    assert event_handoff_ids == handoff_ids
