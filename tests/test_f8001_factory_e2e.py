"""Focused end-to-end lifecycle coverage for the PostgreSQL Factory Runtime."""
import json
import uuid
from pathlib import Path

from engine.agents import GatewayModelAdapter, SandyDeveloperAdapter, SandyQAAdapter
from engine.model_gateway import ModelGateway, ModelRequest, ModelResponse
from engine.postgres_runtime import PostgresRuntime
from engine.runtime import Result
from engine.sandy_adapter import SandYSandboxResult
from infrastructure.repositories.artifacts import ArtifactsRepository
from infrastructure.repositories.events import EventsRepository
from infrastructure.repositories.quality import QualityRepository
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
AGENTS = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')


class _DeterministicProvider:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        if request.metadata.get('agent') == 'developer':
            content = json.dumps({
                'summary': 'Added lifecycle sample',
                'files': [
                    {'path': 'app.py', 'content': 'VALUE = 7\n'},
                    {'path': 'tests/test_app.py', 'content': (
                        'import unittest\nimport app\n'
                        'class AppTests(unittest.TestCase):\n'
                        '    def test_value(self): self.assertEqual(app.VALUE, 7)\n'
                    )},
                ],
            })
        else:
            content = f"{request.metadata.get('agent')} planned"
        return ModelResponse(content)


class _GatewayPatchModel:
    """Keep developer JSON intact while routing the call through ModelGateway."""
    def __init__(self, gateway):
        self.gateway = gateway

    def execute(self, task, project, context):
        response = self.gateway.complete(ModelRequest(
            messages=[{'role': 'user', 'content': 'Return a safe test patch as JSON.'}],
            response_format='json',
            metadata={key: context[key] for key in
                      ('tenant_id', 'project_id', 'task_id', 'run_id')} | {'agent': task['agent']},
            request_id=str(context['run_id']),
        ))
        return Result(True, response.content)


class _SafeSandbox:
    def __init__(self):
        self.calls = []

    def execute_argv(self, workspace, argv, timeout=None):
        self.calls.append((Path(workspace).resolve(), tuple(argv), timeout))
        return SandYSandboxResult(
            True, 'Ran 1 test', '', 0, 10, 'c' * 64, '', 0,
            effective_argv=tuple(argv),
        )


def test_factory_full_lifecycle_persists_gateway_sandy_artifacts_and_quality(
    tmp_path, isolated_postgres,
):
    provider = _DeterministicProvider()
    gateway = ModelGateway(provider)
    sandbox = _SafeSandbox()
    adapters = {agent: GatewayModelAdapter(gateway) for agent in AGENTS}
    adapters['developer'] = SandyDeveloperAdapter(
        sandbox=sandbox, model=_GatewayPatchModel(gateway),
    )
    adapters['qa'] = SandyQAAdapter(sandbox=sandbox)

    runtime = PostgresRuntime(tmp_path, adapters, tenant_id=TENANT_ID)
    project_id = runtime.orchestrator.create_project(
        'F8-001 lifecycle ' + str(uuid.uuid4()),
        'Exercise planning, agent execution, sandbox evidence and quality approval',
    )
    isolated_postgres(project_id)
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)

    history = runtime.run(project_id, max_steps=12)

    assert history[-1]['state'] == 'done'
    assert runtime.brain.get_project(project_id).status == 'done'
    persisted_tasks = runtime.brain.tasks(project_id)
    assert len(tasks) == len(AGENTS)
    assert all(task['status'] == 'done' for task in persisted_tasks)
    task_runs = runtime.runs(project_id)
    assert len(task_runs) == len(AGENTS)
    assert all(run['status'] == 'succeeded' for run in task_runs)

    # Every agent call went through the same mock Model Gateway, including the
    # developer call that generated the patch applied and tested by Sandy.
    # QA consumes and validates Sandy evidence directly; all other agent roles
    # request model output through the shared gateway.
    assert {request.metadata['agent'] for request in provider.requests} == set(AGENTS) - {'qa'}
    assert all(request.metadata['tenant_id'] == str(TENANT_ID) for request in provider.requests)
    assert all(request.metadata['project_id'] == project_id for request in provider.requests)
    assert all(request.metadata.get('task_id') and request.metadata.get('run_id')
               for request in provider.requests)
    assert len(sandbox.calls) == 2

    artifacts = ArtifactsRepository(tenant_id=TENANT_ID).list_artifacts(
        project_id=uuid.UUID(project_id), artifact_type='test',
    )
    developer_run = next(run for run in task_runs
                         if next(t for t in persisted_tasks if t['id'] == run['task_id'])['agent'] == 'developer')
    developer_artifact = next(item for item in artifacts
                              if item.task_run_id == uuid.UUID(developer_run['id']))
    stored, contents = runtime.retrieve_artifact(project_id, developer_artifact.id)
    assert stored.is_verified
    assert stored.sha256_hash == developer_artifact.sha256_hash
    assert b'"runner": "sandy"' in contents
    assert (tmp_path / 'workspaces' / project_id / 'app.py').read_text(encoding='utf-8') == 'VALUE = 7\n'

    validations = QualityRepository(tenant_id=TENANT_ID).list_validations(uuid.UUID(project_id))
    qa_task = next(task for task in persisted_tasks if task['agent'] == 'qa')
    qa_validation = next(item for item in validations if item.task_id == uuid.UUID(qa_task['id']))
    assert qa_validation.status == 'passed'
    assert qa_validation.metrics['approved'] is True

    events = EventsRepository(tenant_id=TENANT_ID).execution_trace(uuid.UUID(project_id))
    assert events
    assert all(event.project_id == uuid.UUID(project_id) for event in events)
    assert any(event.type == 'task.claimed' for event in events)
    assert any(event.type == 'agent.handoff' for event in events)
    assert any(event.type == 'quality.approved' for event in events)
