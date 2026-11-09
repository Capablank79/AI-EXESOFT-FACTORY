"""PostgreSQL functional coverage for the F6-003 execution Quality Gate."""
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

from engine.runtime import Result
from engine.postgres_runtime import PostgresRuntime
from infrastructure.repositories.quality import QualityRepository
from infrastructure.repositories.events import EventsRepository
from infrastructure.uow import UnitOfWork
from quality.evidence import record_evidence
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
AGENTS = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')


def passed_sandy_evidence(workspace):
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    output = 'Ran 1 test'
    return record_evidence(root, {
        'schema': 'exesoft.quality.test.v1', 'id': uuid.uuid4().hex,
        'kind': 'test', 'runner': 'sandy', 'status': 'passed', 'returncode': 0,
        'sandy_exit_code': 0, 'output': output,
        'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
        'tests_run': 1, 'result_sha256': 'a' * 64, 'partial_ok': False,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })


class PassingAdapter:
    def execute(self, task, project, context):
        refs = (passed_sandy_evidence(context['workspace']),) if task['agent'] in {'developer', 'qa'} else ()
        return Result(True, 'Agent completed', refs, approved=task['agent'] == 'qa')


@pytest.fixture
def runtime_workspace():
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


def create_project(runtime):
    project_id = runtime.orchestrator.create_project(
        f'F6-003 Quality Gate {uuid.uuid4()}', 'Validate task execution quality'
    )
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)
    return project_id, tasks


def validations_for(project_id):
    return QualityRepository(tenant_id=TENANT_ID).list_validations(uuid.UUID(project_id))


def test_gate_persists_passed_decisions_and_allows_valid_sandy_completion(runtime_workspace):
    runtime = PostgresRuntime(runtime_workspace,
                              dict.fromkeys(AGENTS, PassingAdapter()), tenant_id=TENANT_ID)
    project_id, _ = create_project(runtime)

    history = runtime.run(project_id, max_steps=10)

    assert history[-1]['state'] == 'done'
    validations = validations_for(project_id)
    assert len(validations) == len(AGENTS)
    assert all(item.status == 'passed' for item in validations)
    assert all(item.metrics['gate'] == 'f6_execution' and item.metrics['approved']
               for item in validations)
    trace = EventsRepository(tenant_id=TENANT_ID).execution_trace(uuid.UUID(project_id))
    assert sum(event.type == 'quality.approved' for event in trace) == len(AGENTS)


def test_gate_rejects_missing_sandbox_evidence_and_keeps_task_incomplete(runtime_workspace):
    class MissingEvidence(PassingAdapter):
        def execute(self, task, project, context):
            if task['agent'] == 'developer':
                return Result(True, 'Claimed tests passed', ('evidence:unverified',))
            return super().execute(task, project, context)

    adapters = dict.fromkeys(AGENTS, MissingEvidence())
    runtime = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    project_id, tasks = create_project(runtime)
    for _ in range(3):
        assert runtime.step(project_id)['state'] == 'done'

    outcome = runtime.step(project_id)

    assert outcome['state'] == 'pending'
    assert 'Quality Gate rechazó' in outcome['summary']
    task = next(item for item in runtime.brain.tasks(project_id)
                if item['id'] == tasks[3]['id'])
    assert task['status'] == 'pending'
    run = next(item for item in runtime.runs(project_id) if item['id'] == outcome['run'])
    assert run['status'] == 'failed'
    decision = next(item for item in validations_for(project_id)
                    if item.task_id == uuid.UUID(tasks[3]['id']))
    assert decision.status == 'failed'
    assert not decision.metrics['approved']
    assert 'evidencia' in decision.metrics['reason'].lower()


def test_recovery_records_gate_failure_for_interrupted_execution(runtime_workspace):
    runtime = PostgresRuntime(runtime_workspace, tenant_id=TENANT_ID)
    project_id, tasks = create_project(runtime)
    task_id = uuid.UUID(tasks[0]['id'])
    project_uuid = uuid.UUID(project_id)
    run_id = uuid.uuid4()
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        task = uow.planning.get_task(task_id)
        claimed = uow.planning.claim_task_for_execution(
            task_id, project_uuid, expected_version=task.version
        )
        assert claimed is not None
        uow.execution.create_task_run(
            tenant_id=TENANT_ID, task_id=task_id, project_id=project_uuid,
            agent_name=tasks[0]['agent'], run_id=run_id
        )
        uow.commit()

    assert runtime.recover(project_id) == 1
    decision = next(item for item in validations_for(project_id)
                    if item.task_id == task_id)
    assert decision.status == 'failed'
    assert decision.metrics['run_id'] == str(run_id)
    assert decision.metrics['checks']['recovered'] is True
    assert decision.metrics['checks']['execution_integrity'] is False
