"""End-to-end hardening of Sandy evidence, persisted artifacts and Quality Gate."""
import hashlib
import json
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
from infrastructure.repositories.artifacts import ArtifactsRepository
from infrastructure.repositories.quality import QualityRepository
from infrastructure.repositories.events import EventsRepository
from infrastructure.uow import UnitOfWork
from quality.evidence import record_evidence
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
AGENTS = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')


def sandy_evidence(workspace):
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    output = 'Ran 1 test'
    return record_evidence(root, {
        'schema': 'exesoft.quality.test.v1', 'id': uuid.uuid4().hex,
        'kind': 'test', 'runner': 'sandy', 'status': 'passed', 'returncode': 0,
        'sandy_exit_code': 0, 'output': output,
        'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
        'tests_run': 1, 'result_sha256': 'f' * 64, 'partial_ok': False,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })


class EvidenceAdapter:
    def execute(self, task, project, context):
        evidence = (sandy_evidence(context['workspace']),) if task['agent'] in {'developer', 'qa'} else ()
        return Result(True, 'Validated', evidence, approved=task['agent'] == 'qa')


@pytest.fixture
def runtime_workspace():
    with tempfile.TemporaryDirectory() as folder:
        yield Path(folder)


def project_with_runtime(root):
    runtime = PostgresRuntime(root, dict.fromkeys(AGENTS, EvidenceAdapter()), tenant_id=TENANT_ID)
    project_id = runtime.orchestrator.create_project(
        f'F6-004 Pipeline {uuid.uuid4()}', 'Validate Sandy artifact gating end to end'
    )
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)
    return runtime, project_id, tasks


def test_valid_sandy_evidence_is_correlated_to_artifact_run_and_gate(runtime_workspace):
    runtime, project_id, tasks = project_with_runtime(runtime_workspace)
    result = runtime.run(project_id, max_steps=10)
    assert result[-1]['state'] == 'done'

    developer_task_id = uuid.UUID(tasks[3]['id'])
    developer_run = next(item for item in runtime.runs(project_id)
                         if item['task_id'] == str(developer_task_id))
    evidence_ref = json.loads(developer_run['evidence'])[0]
    artifacts = ArtifactsRepository(tenant_id=TENANT_ID).list_artifacts(
        project_id=uuid.UUID(project_id), artifact_type='test'
    )
    artifact = next(item for item in artifacts if item.task_run_id == uuid.UUID(developer_run['id']))
    assert artifact.path == evidence_ref.replace('\\', '/')
    assert artifact.project_id == uuid.UUID(project_id)
    assert artifact.tenant_id == TENANT_ID
    retrieved, content = runtime.retrieve_artifact(project_id, artifact.id)
    assert retrieved.sha256_hash == hashlib.sha256(content).hexdigest()
    decision = next(item for item in QualityRepository(tenant_id=TENANT_ID).list_validations(
        uuid.UUID(project_id)) if item.task_id == developer_task_id)
    assert decision.status == 'passed'
    assert decision.metrics['run_id'] == developer_run['id']
    assert decision.metrics['checks']['artifact_persisted'] is True
    with pytest.raises(PermissionError):
        runtime.retrieve_artifact(str(uuid.uuid4()), artifact.id)


def test_integrity_change_between_gate_and_artifact_insert_blocks_completion(runtime_workspace, monkeypatch):
    runtime, project_id, tasks = project_with_runtime(runtime_workspace)
    for _ in range(3):
        assert runtime.step(project_id)['state'] == 'done'

    original_register = ArtifactsRepository.register_workspace_artifact

    def mutate_then_register(repository, workspace, relative_path, **kwargs):
        evidence_file = Path(workspace) / relative_path
        evidence_file.write_bytes(evidence_file.read_bytes() + b' altered during persistence')
        return original_register(repository, workspace, relative_path, **kwargs)

    monkeypatch.setattr(ArtifactsRepository, 'register_workspace_artifact', mutate_then_register)
    result = runtime.step(project_id)

    assert result['state'] == 'pending'
    assert 'Quality Gate rechazó' in result['summary']
    task = next(item for item in runtime.brain.tasks(project_id) if item['id'] == tasks[3]['id'])
    assert task['status'] == 'pending'
    validation = next(item for item in QualityRepository(tenant_id=TENANT_ID).list_validations(
        uuid.UUID(project_id)) if item.task_id == uuid.UUID(tasks[3]['id']))
    assert validation.status == 'failed'
    assert validation.metrics['checks']['artifact_persisted'] is False
    trace = EventsRepository(tenant_id=TENANT_ID).execution_trace(
        uuid.UUID(project_id), uuid.UUID(tasks[3]['id'])
    )
    assert any(event.type == 'quality.rejected' for event in trace)
    assert not any(event.type == 'task.succeeded' for event in trace)


def test_artifact_and_quality_decision_roll_back_together(runtime_workspace, monkeypatch):
    runtime, project_id, tasks = project_with_runtime(runtime_workspace)
    for _ in range(3):
        assert runtime.step(project_id)['state'] == 'done'

    original_update = QualityRepository.update_validation_status

    def fail_validation(*args, **kwargs):
        raise RuntimeError('simulated Quality Gate persistence error')

    monkeypatch.setattr(QualityRepository, 'update_validation_status', fail_validation)
    with pytest.raises(RuntimeError, match='Quality Gate persistence error'):
        runtime.step(project_id)
    monkeypatch.setattr(QualityRepository, 'update_validation_status', original_update)

    task = next(item for item in runtime.brain.tasks(project_id) if item['id'] == tasks[3]['id'])
    assert task['status'] == 'running'
    assert not ArtifactsRepository(tenant_id=TENANT_ID).list_artifacts(
        project_id=uuid.UUID(project_id), artifact_type='test'
    )
    assert runtime.recover(project_id) == 1
    task = next(item for item in runtime.brain.tasks(project_id) if item['id'] == tasks[3]['id'])
    assert task['status'] == 'pending'
