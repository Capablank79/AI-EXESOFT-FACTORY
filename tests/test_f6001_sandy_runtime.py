"""PostgreSQL Runtime integration for the existing Sandy adapter interfaces."""
import json
import os
import sys
import tempfile
import uuid
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

from engine import agent_runtime as agent_runtime_module
from engine.agent_runtime import AgentRuntime, UnavailableAdapter
from engine.agents import SandyDeveloperAdapter, SandyQAAdapter
from engine.postgres_runtime import PostgresRuntime
from engine.runtime import Result
from engine.sandy_adapter import SandYSandboxResult
from infrastructure.repositories.events import EventsRepository
from infrastructure.repositories.artifacts import ArtifactsRepository
from infrastructure.repositories.quality import QualityRepository
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
AGENTS = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


class MockSandbox:
    def __init__(self, result=None):
        self.result = result or SandYSandboxResult(
            True, 'Ran 1 test', '', 0, 18, 'a' * 64, '', 0,
        )
        self.calls = []

    def execute_argv(self, workspace, argv, timeout=None):
        self.calls.append((Path(workspace).resolve(), tuple(argv), timeout))
        return SandYSandboxResult(
            success=self.result.success, stdout=self.result.stdout, stderr=self.result.stderr,
            returncode=self.result.returncode, duration_ms=self.result.duration_ms,
            sha256=self.result.sha256, error=self.result.error,
            sandy_exit_code=self.result.sandy_exit_code,
            effective_argv=tuple(argv),
        )


class PatchModel:
    def execute(self, task, project, context):
        patch = {
            'summary': 'Added safe module',
            'files': [
                {'path': 'app.py', 'content': 'VALUE = 7\n'},
                {'path': 'tests/test_app.py', 'content': (
                    'import unittest\nimport app\n'
                    'class AppTests(unittest.TestCase):\n'
                    '    def test_value(self): self.assertEqual(app.VALUE, 7)\n'
                )},
            ],
        }
        return Result(True, json.dumps(patch))


class SuccessAdapter:
    def execute(self, task, project, context):
        return Result(True, f"{task['agent']} completed", (f"artifact:{task['agent']}",))


def create_project(runtime, register_project=None):
    project_id = runtime.orchestrator.create_project(
        f'F6-001 Sandy {uuid.uuid4()}', 'Validate isolated test execution and artifact capture'
    )
    if register_project:
        register_project(project_id)
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(runtime.tenant_id, project_id)
    return project_id, tasks


def test_agent_runtime_defaults_to_sandy_for_code_execution_and_fails_closed(workspace, monkeypatch):
    class AvailableSandbox:
        _available = True

    available = AvailableSandbox()
    monkeypatch.setattr(agent_runtime_module, 'SandySandboxAdapter', lambda: available)
    monkeypatch.setattr(agent_runtime_module, 'configured_model_adapter', lambda: SuccessAdapter())
    runtime = AgentRuntime(workspace, tenant_id=TENANT_ID)
    assert isinstance(runtime, PostgresRuntime)
    assert isinstance(runtime.adapters['developer'], SandyDeveloperAdapter)
    assert isinstance(runtime.adapters['qa'], SandyQAAdapter)
    assert runtime.adapters['developer'].sandbox is available
    assert runtime.adapters['qa'].sandbox is available

    class UnavailableSandbox:
        _available = False

    monkeypatch.setattr(agent_runtime_module, 'SandySandboxAdapter', UnavailableSandbox)
    blocked = AgentRuntime(workspace, tenant_id=TENANT_ID)
    assert isinstance(blocked.adapters['developer'], UnavailableAdapter)
    assert isinstance(blocked.adapters['qa'], UnavailableAdapter)


def test_postgres_multi_agent_pipeline_records_sandy_logs_exit_codes_and_artifacts(workspace, isolated_postgres):
    sandbox = MockSandbox()
    adapters = {agent: SuccessAdapter() for agent in AGENTS}
    adapters['developer'] = SandyDeveloperAdapter(sandbox=sandbox, model=PatchModel())
    adapters['qa'] = SandyQAAdapter(sandbox=sandbox)
    runtime = AgentRuntime(workspace, adapters, tenant_id=TENANT_ID)
    project_id, tasks = create_project(runtime, isolated_postgres)

    history = runtime.run(project_id, max_steps=10)
    assert history[-1]['state'] == 'done'
    assert len(sandbox.calls) == 2
    for sandbox_workspace, argv, timeout in sandbox.calls:
        assert sandbox_workspace == (workspace / 'workspaces' / project_id).resolve()
        assert argv[0] == sys.executable
        assert argv[1:4] == ('-I', '-m', 'unittest')
        assert timeout == 120
        assert '--' not in argv

    task_runs = runtime.runs(project_id)
    developer_run = next(run for run in task_runs if run['task_id'] == tasks[3]['id'])
    evidence_path = json.loads(developer_run['evidence'])[0]
    evidence = json.loads((workspace / 'workspaces' / project_id / evidence_path).read_text())
    assert evidence['runner'] == 'sandy'
    assert evidence['returncode'] == 0
    assert evidence['sandy_exit_code'] == 0
    assert evidence['command'] == list(sandbox.calls[0][1])
    assert evidence['result_sha256'] == 'a' * 64
    registered = ArtifactsRepository(tenant_id=TENANT_ID).list_artifacts(
        project_id=uuid.UUID(project_id), artifact_type='test'
    )
    developer_artifact = next(item for item in registered
                              if item.task_run_id == uuid.UUID(developer_run['id']))
    stored, artifact_bytes = runtime.retrieve_artifact(project_id, developer_artifact.id)
    assert stored.sha256_hash == developer_artifact.sha256_hash
    assert artifact_bytes == (workspace / 'workspaces' / project_id / evidence_path).read_bytes()
    assert stored.is_verified
    assert 'command' not in (stored.metadata or {})
    assert 'output' not in (stored.metadata or {})
    with pytest.raises(PermissionError):
        ArtifactsRepository(tenant_id=uuid.uuid4()).retrieve_workspace_artifact(
            developer_artifact.id, project_id=uuid.UUID(project_id),
            workspace=workspace / 'workspaces' / project_id
        )
    artifact_file = workspace / 'workspaces' / project_id / evidence_path
    original_bytes = artifact_file.read_bytes()
    try:
        artifact_file.write_bytes(original_bytes + b' tampered')
        with pytest.raises(ValueError, match='integrity'):
            runtime.retrieve_artifact(project_id, developer_artifact.id)
    finally:
        artifact_file.write_bytes(original_bytes)
    handoffs = runtime.handoffs(project_id)
    developer_handoff = next(item for item in handoffs if item['from_agent'] == 'developer')
    assert developer_handoff['artifacts'] == [evidence_path]

    events = EventsRepository(tenant_id=TENANT_ID).execution_trace(uuid.UUID(project_id))
    developer_events = [event for event in events if event.task_id == uuid.UUID(tasks[3]['id'])]
    completed = next(event for event in developer_events if event.type == 'task.succeeded')
    handoff = next(event for event in developer_events if event.type == 'agent.handoff')
    assert completed.detail['run_id'] == handoff.detail['run_id']
    assert handoff.detail['handoff_id'] == developer_handoff['handoff_id']


@pytest.mark.parametrize(
    ('returncode', 'error', 'stderr'),
    [(125, 'executor_error', 'AppContainer execution denied'),
     (130, 'timeout', 'Sandy execution timed out')],
)
def test_sandy_failure_stays_failed_and_never_falls_back_to_host_execution(
    workspace, returncode, error, stderr, isolated_postgres
):
    failed_sandbox = MockSandbox(SandYSandboxResult(
        False, '', stderr, returncode, 9, 'b' * 64, error, returncode,
    ))
    adapters = {agent: SuccessAdapter() for agent in AGENTS}
    adapters['developer'] = SandyDeveloperAdapter(sandbox=failed_sandbox, model=PatchModel())
    adapters['qa'] = SandyQAAdapter(sandbox=failed_sandbox)
    runtime = AgentRuntime(workspace, adapters, tenant_id=TENANT_ID)
    project_id, tasks = create_project(runtime, isolated_postgres)

    for _ in range(3):
        outcome = runtime.step(project_id)
        assert outcome['state'] == 'done'
    failed = runtime.step(project_id)
    assert failed['state'] == 'pending'
    assert failed['task'] == tasks[3]['id']
    assert f'Sandy exit={returncode}' in failed['summary']
    assert len(failed_sandbox.calls) == 1
    assert len(runtime.handoffs(project_id)) == 3
    developer_task = next(task for task in runtime.brain.tasks(project_id)
                          if task['id'] == tasks[3]['id'])
    assert developer_task['status'] == 'pending'
    developer_runs = [run for run in runtime.runs(project_id) if run['task_id'] == tasks[3]['id']]
    assert developer_runs[0]['status'] == 'failed'
    assert stderr in developer_runs[0]['summary']
    evidence_path = json.loads(developer_runs[0]['evidence'])[0]
    evidence = json.loads((workspace / 'workspaces' / project_id / evidence_path).read_text())
    assert evidence['sandy_exit_code'] == returncode
    assert evidence['error'] == error
    developer_validation = next(
        item for item in QualityRepository(tenant_id=TENANT_ID).list_validations(
            uuid.UUID(project_id)
        ) if item.task_id == uuid.UUID(tasks[3]['id'])
    )
    assert developer_validation.status == 'failed'
    assert developer_validation.metrics['approved'] is False
    failed_artifacts = ArtifactsRepository(tenant_id=TENANT_ID).list_artifacts(
        project_id=uuid.UUID(project_id), artifact_type='test'
    )
    assert any(item.task_run_id == uuid.UUID(developer_runs[0]['id'])
               for item in failed_artifacts)


def test_artifact_paths_reject_traversal_and_tampering(workspace):
    from infrastructure.repositories.artifacts import ArtifactsRepository

    artifact_repo = ArtifactsRepository(tenant_id=TENANT_ID)
    (workspace / 'inside.txt').write_text('original', encoding='utf-8')
    with pytest.raises(ValueError):
        artifact_repo._workspace_file(workspace, '../outside.txt')
    resolved_root, resolved_file = artifact_repo._workspace_file(workspace, 'inside.txt')
    assert resolved_root == workspace.resolve()
    assert resolved_file == (workspace / 'inside.txt').resolve()

