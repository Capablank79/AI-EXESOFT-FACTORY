"""Idempotent interrupted-run recovery using the guarded test database fixture."""
import uuid
from pathlib import Path

from engine.postgres_runtime import PostgresRuntime
from engine.runtime import Result
from infrastructure.repositories.artifacts import ArtifactsRepository
from infrastructure.repositories.events import EventsRepository
from infrastructure.repositories.quality import QualityRepository
from infrastructure.uow import UnitOfWork
from plan_approval_test_support import approve_test_plan

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


def test_repeated_recovery_then_resume_preserves_single_audit_history(
    tmp_path, isolated_postgres,
):
    class CountingAdapter:
        calls = 0

        def execute(self, task, project, context):
            self.calls += 1
            artifact = Path(context['workspace']) / 'artifacts' / 'resumed.txt'
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text('resumed once', encoding='utf-8')
            return Result(True, 'Resumed after recovery', ('artifacts/resumed.txt',))

    adapter = CountingAdapter()
    runtime = PostgresRuntime(tmp_path, {'pm': adapter}, tenant_id=TENANT_ID)
    project_id = runtime.orchestrator.create_project(
        'F8-003 recovery ' + str(uuid.uuid4()),
        'Verify idempotent crash recovery and safe task resumption',
    )
    isolated_postgres(project_id)
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)
    task_id = uuid.UUID(tasks[0]['id'])
    project_uuid = uuid.UUID(project_id)
    interrupted_run_id = uuid.uuid4()

    # Persist the same claimed/running state and trace a worker would leave if
    # the process stopped after committing its claim but before completion.
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        current = uow.planning.get_task(task_id)
        claimed = uow.planning.claim_task_for_execution(
            task_id, project_uuid, expected_version=current.version,
        )
        assert claimed is not None
        uow.execution.create_task_run(
            tenant_id=TENANT_ID, task_id=task_id, project_id=project_uuid,
            agent_name=tasks[0]['agent'], run_id=interrupted_run_id,
        )
        uow.events.emit_event(
            'task.claimed', {'run_id': str(interrupted_run_id), 'attempt': claimed.attempts},
            tenant_id=TENANT_ID, project_id=project_uuid, task_id=task_id,
        )
        uow.events.emit_event(
            'task.started', {'run_id': str(interrupted_run_id), 'attempt': claimed.attempts},
            tenant_id=TENANT_ID, project_id=project_uuid, task_id=task_id,
        )
        uow.commit()

    restarted = PostgresRuntime(tmp_path, {'pm': adapter}, tenant_id=TENANT_ID)
    assert restarted.recover(project_id) == 1
    assert restarted.recover(project_id) == 0

    recovered_validation = QualityRepository(tenant_id=TENANT_ID).list_validations(project_uuid)
    assert len(recovered_validation) == 1
    assert recovered_validation[0].status == 'failed'
    assert recovered_validation[0].metrics['checks']['recovered'] is True
    assert recovered_validation[0].metrics['approved'] is False

    history = restarted.run(project_id, max_steps=1)
    assert history[-1]['state'] == 'done'
    assert adapter.calls == 1
    task = next(item for item in restarted.brain.tasks(project_id)
                if item['id'] == str(task_id))
    assert task['status'] == 'done'
    assert task['attempts'] == 2

    runs = restarted.runs(project_id)
    assert len(runs) == 2
    assert sum(item['status'] == 'interrupted' for item in runs) == 1
    assert sum(item['status'] == 'succeeded' for item in runs) == 1

    validations = QualityRepository(tenant_id=TENANT_ID).list_validations(project_uuid)
    assert sum(item.status == 'failed' for item in validations) == 1
    assert sum(item.status == 'passed' for item in validations) == 1
    assert next(item for item in validations if item.status == 'passed').metrics['approved'] is True

    artifacts = ArtifactsRepository(tenant_id=TENANT_ID).list_artifacts(
        project_id=project_uuid, artifact_type='test',
    )
    succeeded_run = next(item for item in runs if item['status'] == 'succeeded')
    assert len(artifacts) == 1
    assert artifacts[0].task_run_id == uuid.UUID(succeeded_run['id'])
    stored, content = restarted.retrieve_artifact(project_id, artifacts[0].id)
    assert stored.is_verified
    assert content == b'resumed once'

    trace = EventsRepository(tenant_id=TENANT_ID).execution_trace(project_uuid, task_id)
    assert sum(event.type == 'task.recovered' for event in trace) == 1
    assert sum(event.type == 'quality.rejected' for event in trace) == 1
    assert sum(event.type == 'quality.approved' for event in trace) == 1
    assert sum(event.type == 'task.claimed' for event in trace) == 2
    assert sum(event.type == 'task.started' for event in trace) == 2
    assert len({event.detail['run_id'] for event in trace
                if event.type in {'task.claimed', 'task.started'}}) == 2
