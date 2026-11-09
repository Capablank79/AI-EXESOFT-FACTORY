"""PostgreSQL tests for persistent, idempotent agent-to-agent handoffs."""
import json
import hashlib
import os
import tempfile
import uuid
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
from engine.handoffs import AgentHandoff
from engine.postgres_runtime import PostgresRuntime
from engine.runtime import Result
from infrastructure.repositories.events import EventsRepository
from infrastructure.repositories.memory import MemoryRepository
from quality.evidence import record_evidence
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
AGENTS = ['pm', 'architect', 'ux', 'developer', 'qa', 'devops']


class HandoffRecorder:
    def __init__(self):
        self.contexts = {}

    def execute(self, task, project, context):
        self.contexts.setdefault(task['agent'], []).append(context)
        evidence = (f"artifact:{task['agent']}:verified",)
        if task['agent'] in {'developer', 'qa'}:
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
            True, f"{task['agent']} decision recorded",
            evidence, approved=task['agent'] == 'qa',
        )


def make_adapters(recorder):
    return {agent: recorder for agent in AGENTS}


def create_project(runtime):
    project_id = runtime.orchestrator.create_project(
        f"F5-003 Handoff {uuid.uuid4()}", 'Keep decisions and artifacts available to each specialist'
    )
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)
    return project_id, tasks


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


def test_handoffs_resume_context_across_runtime_restart(workspace):
    first_recorder = HandoffRecorder()
    runtime = AgentRuntime(workspace, make_adapters(first_recorder), tenant_id=TENANT_ID)
    project_id, tasks = create_project(runtime)

    first = runtime.step(project_id)
    assert first['state'] == 'done'
    first_handoff = runtime.handoffs(project_id)
    assert len(first_handoff) == 1
    assert first_handoff[0]['from_agent'] == 'pm'
    assert first_handoff[0]['to_agents'] == ['architect']
    assert first_handoff[0]['artifacts'] == ['artifact:pm:verified']
    assert first_handoff[0]['task_context']['status'] == 'done'
    assert first_handoff[0]['project_context']['request'] == runtime._project(project_id)['request']

    restarted_recorder = HandoffRecorder()
    restarted = AgentRuntime(workspace, make_adapters(restarted_recorder), tenant_id=TENANT_ID)
    history = restarted.run(project_id, max_steps=10)
    assert history[-1]['state'] == 'done'
    assert len(restarted.handoffs(project_id)) == 6
    architect_context = restarted_recorder.contexts['architect'][0]
    assert architect_context['handoffs'][0]['handoff_id'] == first_handoff[0]['handoff_id']
    assert architect_context['handoffs'][0]['decisions'][0]['agent'] == 'pm'
    assert all(task['status'] == 'done' for task in restarted.brain.tasks(project_id))

    events = EventsRepository(tenant_id=TENANT_ID).execution_trace(
        uuid.UUID(project_id), uuid.UUID(tasks[0]['id'])
    )
    handoff_event = next(event for event in events if event.type == 'agent.handoff')
    assert handoff_event.detail['handoff_id'] == first_handoff[0]['handoff_id']
    assert handoff_event.detail['run_id'] == first['run']
    assert handoff_event.tenant_id == TENANT_ID


def test_handoff_idempotency_and_transition_validation(workspace):
    recorder = HandoffRecorder()
    runtime = AgentRuntime(workspace, make_adapters(recorder), tenant_id=TENANT_ID)
    project_id, _ = create_project(runtime)
    runtime.step(project_id)
    payload = runtime.handoffs(project_id)[0]
    handoff = AgentHandoff.from_dict(payload)

    original = MemoryRepository(tenant_id=TENANT_ID).create_agent_handoff(handoff)
    existing = runtime.handoffs(project_id)
    assert len(existing) == 1
    assert original.body == json.dumps(payload, ensure_ascii=False, sort_keys=True)

    invalid = dict(payload, task_status='running')
    with pytest.raises(ValueError, match='completed tasks'):
        AgentHandoff.from_dict(invalid)


def test_handoff_and_completion_roll_back_together_then_recover(workspace, monkeypatch):
    recorder = HandoffRecorder()
    runtime = PostgresRuntime(workspace, make_adapters(recorder), tenant_id=TENANT_ID)
    project_id, tasks = create_project(runtime)

    def fail_handoff(*args, **kwargs):
        raise RuntimeError('simulated handoff persistence failure')

    monkeypatch.setattr(MemoryRepository, 'create_agent_handoff', fail_handoff)
    with pytest.raises(RuntimeError, match='simulated handoff persistence failure'):
        runtime.step(project_id)

    task = next(item for item in runtime.brain.tasks(project_id) if item['id'] == tasks[0]['id'])
    assert task['status'] == 'running'
    assert runtime.handoffs(project_id) == []
    events = EventsRepository(tenant_id=TENANT_ID).execution_trace(
        uuid.UUID(project_id), uuid.UUID(tasks[0]['id'])
    )
    assert not any(event.type == 'task.succeeded' or event.type == 'agent.handoff' for event in events)

    monkeypatch.undo()
    restarted = PostgresRuntime(workspace, make_adapters(recorder), tenant_id=TENANT_ID)
    assert restarted.recover(project_id) == 1
    retried = restarted.step(project_id)
    assert retried['state'] == 'done'
    assert len(restarted.handoffs(project_id)) == 1
    runs = restarted.runs(project_id)
    assert len(runs) == 2
    assert {run['status'] for run in runs} == {'succeeded', 'interrupted'}

