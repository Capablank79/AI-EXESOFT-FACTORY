"""CLI integration tests against the PostgreSQL-backed Factory Runtime."""
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

import factory
from engine.runtime import Result
from infrastructure.uow import UnitOfWork
from quality.evidence import record_evidence
from plan_approval_test_support import approve_test_plan, install_mock_planner

TENANT_ID = 'b66f93cd-2f3a-4215-a2ac-93a59a84b4d2'


def invoke_cli(root, *args, capsys, tenant_id=TENANT_ID):
    code = factory.main(['--root', str(root), '--tenant-id', tenant_id, *args])
    captured = capsys.readouterr()
    return code, captured


def test_cli_project_task_status_and_run_queries_are_tenant_scoped(capsys, monkeypatch):
    install_mock_planner(monkeypatch)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        code, output = invoke_cli(root, 'create', 'CLI Project', 'Exercise PostgreSQL CLI', capsys=capsys)
        assert code == 0
        project_id = json.loads(output.out)['project_id']

        code, output = invoke_cli(root, 'plan', project_id, capsys=capsys)
        assert code == 0
        assert len(json.loads(output.out)['tasks']) == 6

        code, output = invoke_cli(root, 'status', project_id, capsys=capsys)
        assert code == 0
        status = json.loads(output.out)
        assert status['project']['id'] == project_id
        assert len(status['tasks']) == 6

        task_id = status['tasks'][0]['id']
        code, output = invoke_cli(root, 'task', project_id, task_id, capsys=capsys)
        assert code == 0
        assert json.loads(output.out)['task']['id'] == task_id

        code, output = invoke_cli(root, 'tasks', project_id, capsys=capsys)
        assert code == 0
        assert len(json.loads(output.out)['tasks']) == 6

        code, output = invoke_cli(root, 'runs', project_id, capsys=capsys)
        assert code == 0
        assert json.loads(output.out)['runs'] == []

        code, output = invoke_cli(root, 'projects', capsys=capsys)
        assert code == 0
        assert any(item['id'] == project_id for item in json.loads(output.out)['projects'])


def test_cli_run_and_resume_use_postgres_runtime_quality_gate(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)

        class MockAgent:
            def execute(self, task, project, context):
                if task['agent'] not in {'developer', 'qa'}:
                    return Result(True, 'Mock completed')
                workspace = Path(context['workspace'])
                workspace.mkdir(parents=True, exist_ok=True)
                output = 'Ran 1 test'
                evidence = record_evidence(workspace, {
                    'schema': 'exesoft.quality.test.v1', 'id': uuid.uuid4().hex,
                    'kind': 'test', 'runner': 'sandy', 'status': 'passed',
                    'returncode': 0, 'sandy_exit_code': 0, 'output': output,
                    'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
                    'tests_run': 1, 'result_sha256': 'a' * 64, 'partial_ok': False,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                })
                return Result(True, 'Mock Sandy execution passed', (evidence,),
                              approved=task['agent'] == 'qa')

        monkeypatch.setattr(factory, '_default_execution_adapters',
                            lambda root, project_id, sandbox=None: (
                                dict.fromkeys(('pm', 'architect', 'ux', 'developer', 'qa', 'devops'),
                                              MockAgent()), None))
        monkeypatch.setattr(factory, 'AgentRuntime', factory.PostgresRuntime)
        code, output = invoke_cli(root, 'run', 'CLI Run', 'Run through PostgreSQL', capsys=capsys)
        assert code == 2
        result = json.loads(output.out)
        assert result['status'] == 'planned'
        assert result['execution_status'] == 'approval_required'
        assert all(task['status'] == 'pending' for task in result['tasks'])

        code, output = invoke_cli(root, 'approve', result['project_id'], result['plan_hash'], capsys=capsys)
        assert code == 0 and json.loads(output.out)['approved'] is True

        code, output = invoke_cli(root, 'resume', result['project_id'], capsys=capsys)
        assert code == 0
        resumed = json.loads(output.out)
        assert resumed['status'] == 'done'
        assert all(task['status'] == 'done' for task in resumed['tasks'])
        assert any(item['status'] == 'passed' for item in resumed['quality_decisions'])


def test_cli_hides_database_exception_details_and_returns_error_code(monkeypatch, capsys):
    def fail_runtime(*args, **kwargs):
        raise psycopg.OperationalError('password=must-not-be-printed')

    monkeypatch.setattr(factory, 'PostgresRuntime', fail_runtime)
    code, output = invoke_cli(Path('.'), 'projects', capsys=capsys)
    assert code == 3
    assert 'PostgreSQL operation failed' in output.err
    assert 'must-not-be-printed' not in output.err


def test_cli_rejects_projects_outside_selected_tenant(capsys):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        code, output = invoke_cli(root, 'create', 'Owned Project', 'Tenant boundary', capsys=capsys)
        assert code == 0
        project_id = json.loads(output.out)['project_id']
        other_tenant = str(uuid.uuid4())
        code, output = invoke_cli(root, 'status', project_id, capsys=capsys,
                                  tenant_id=other_tenant)
        assert code == 2
        assert 'not found in this tenant' in output.err


def test_resume_preflight_failure_does_not_recover_or_consume_claim(monkeypatch):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        runtime = factory.PostgresRuntime(root, tenant_id=uuid.UUID(TENANT_ID))
        project_id = runtime.orchestrator.create_project('Resume Safe', 'Do not recover without Sandy')
        tasks = runtime.orchestrator.plan_skeleton(project_id)
        approve_test_plan(uuid.UUID(TENANT_ID), project_id)
        task_id, project_uuid, run_id = uuid.UUID(tasks[0]['id']), uuid.UUID(project_id), uuid.uuid4()
        with UnitOfWork(tenant_id=uuid.UUID(TENANT_ID)) as uow:
            task = uow.planning.get_task(task_id)
            claimed = uow.planning.claim_task_for_execution(
                task_id, project_uuid, expected_version=task.version
            )
            assert claimed is not None
            uow.execution.create_task_run(
                tenant_id=uuid.UUID(TENANT_ID), task_id=task_id,
                project_id=project_uuid, agent_name=tasks[0]['agent'], run_id=run_id
            )
            uow.commit()

        monkeypatch.setattr(factory, '_default_execution_adapters',
                            lambda root, project_id, sandbox=None: (None, 'Sandy unavailable'))
        result = factory.resume_real_task(
            project_id, root=root, tenant_id=uuid.UUID(TENANT_ID)
        )
        assert result['execution_status'] == 'blocked'
        assert result['preflight'] == 'Sandy unavailable'
        assert next(item for item in runtime.brain.tasks(project_id)
                    if item['id'] == str(task_id))['status'] == 'running'
        assert next(item for item in runtime.runs(project_id)
                    if item['id'] == str(run_id))['status'] == 'running'
