"""Concurrent project and tenant isolation coverage for PostgreSQL Runtime."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import uuid

from engine.postgres_runtime import PostgresRuntime
from engine.runtime import Result
from infrastructure.repositories.artifacts import ArtifactsRepository
from infrastructure.repositories.events import EventsRepository
from infrastructure.uow import UnitOfWork
from plan_approval_test_support import approve_test_plan

TEST_TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


def test_simultaneous_tenants_keep_projects_runs_events_and_artifacts_isolated(
    tmp_path, isolated_postgres,
):
    secondary_name = 'F8-002 synthetic tenant ' + str(uuid.uuid4())
    secondary_slug = 'f8-002-' + uuid.uuid4().hex
    with UnitOfWork(tenant_id=TEST_TENANT_ID) as uow:
        secondary_tenant = uow.identity.create_tenant(secondary_name, secondary_slug)
        uow.commit()
    isolated_postgres.register_tenant(
        secondary_tenant.id, secondary_name, secondary_slug,
    )

    barrier = threading.Barrier(2)
    contexts = []
    contexts_lock = threading.Lock()

    class ConcurrentArtifactAdapter:
        def execute(self, task, project, context):
            artifact_path = Path(context['workspace']) / 'artifacts' / 'result.txt'
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            content = f"{context['tenant_id']}|{context['project_id']}|{context['task_id']}"
            artifact_path.write_text(content, encoding='utf-8')
            with contexts_lock:
                contexts.append(dict(context))
            barrier.wait(timeout=20)
            return Result(True, 'concurrent completion', ('artifacts/result.txt',))

    adapter = ConcurrentArtifactAdapter()
    tenants = (TEST_TENANT_ID, secondary_tenant.id)
    runtimes = [
        PostgresRuntime(tmp_path, {'pm': adapter}, tenant_id=tenant_id)
        for tenant_id in tenants
    ]
    project_ids = []
    task_ids = []
    for index, (runtime, tenant_id) in enumerate(zip(runtimes, tenants)):
        project_id = runtime.orchestrator.create_project(
            f'F8-002 concurrent project {index} {uuid.uuid4()}',
            'Verify concurrent tenant/project isolation',
        )
        isolated_postgres(project_id, tenant_id)
        tasks = runtime.orchestrator.plan_skeleton(project_id)
        approve_test_plan(tenant_id, project_id)
        project_ids.append(project_id)
        task_ids.append(tasks[0]['id'])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime.step, project_id)
                   for runtime, project_id in zip(runtimes, project_ids)]
        outcomes = [future.result(timeout=45) for future in futures]

    assert all(outcome['state'] == 'done' for outcome in outcomes)
    assert len(contexts) == 2
    assert {(item['tenant_id'], item['project_id'], item['task_id']) for item in contexts} == {
        (str(tenant_id), project_id, task_id)
        for tenant_id, project_id, task_id in zip(tenants, project_ids, task_ids)
    }

    artifact_records = []
    for index, (runtime, tenant_id, project_id, task_id) in enumerate(
        zip(runtimes, tenants, project_ids, task_ids)
    ):
        project_uuid = uuid.UUID(project_id)
        task_uuid = uuid.UUID(task_id)
        own_project = runtime.brain.get_project(project_id)
        assert own_project is not None
        assert str(own_project.tenant_id) == str(tenant_id)
        assert runtime.brain.get_project(project_ids[1 - index]) is None
        assert runtime.runs(project_id)
        assert runtime.runs(project_ids[1 - index]) == []

        own_events = EventsRepository(tenant_id=tenant_id).execution_trace(
            project_uuid, task_uuid,
        )
        assert own_events
        assert all(event.tenant_id == tenant_id and event.project_id == project_uuid
                   and event.task_id == task_uuid for event in own_events)
        foreign_events = EventsRepository(tenant_id=tenant_id).execution_trace(
            uuid.UUID(project_ids[1 - index]),
        )
        assert foreign_events == []

        own_artifacts = ArtifactsRepository(tenant_id=tenant_id).list_artifacts(
            project_id=project_uuid, artifact_type='test',
        )
        assert len(own_artifacts) == 1
        record = own_artifacts[0]
        artifact_records.append(record)
        assert record.tenant_id == tenant_id
        assert record.project_id == project_uuid
        task_run = next(run for run in runtime.runs(project_id)
                        if run['task_id'] == str(task_uuid))
        assert record.task_run_id == uuid.UUID(task_run['id'])
        stored, contents = runtime.retrieve_artifact(project_id, record.id)
        assert stored.is_verified
        assert contents.decode('utf-8') == (
            f'{tenant_id}|{project_id}|{task_id}'
        )

    assert artifact_records[0].id != artifact_records[1].id
    for index, tenant_id in enumerate(tenants):
        foreign_records = ArtifactsRepository(tenant_id=tenant_id).list_artifacts(
            project_id=uuid.UUID(project_ids[1 - index]), artifact_type='test',
        )
        assert foreign_records == []
