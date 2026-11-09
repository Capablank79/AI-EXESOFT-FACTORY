"""F4-001 functional coverage for PostgreSQL runtime persistence and recovery."""
import os
import tempfile
import threading
import uuid
import hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from engine.runtime import Result
from engine.postgres_runtime import PostgresRuntime
from infrastructure.repositories import IdentityRepository
from infrastructure.repositories.events import EventsRepository
from infrastructure.uow import UnitOfWork
from quality.evidence import record_evidence
from plan_approval_test_support import approve_test_plan


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


class SuccessAdapter:
    def execute(self, task, project, context):
        evidence = ('evidence:verified',)
        if task['agent'] in {'developer', 'qa'}:
            evidence = (make_sandy_evidence(context['workspace']),)
        return Result(True, 'Completed', evidence, approved=True)


def make_sandy_evidence(workspace):
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


@pytest.fixture
def runtime_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def create_planned_project(runtime):
    project_id = runtime.orchestrator.create_project(
        f'F4-001 Runtime {uuid.uuid4()}', 'Validate durable runtime persistence'
    )
    tasks = runtime.orchestrator.plan_skeleton(project_id)
    approve_test_plan(TENANT_ID, project_id)
    return project_id, tasks


def test_pipeline_records_are_persistent_across_runtime_restart(runtime_workspace):
    adapters = dict.fromkeys(
        ['pm', 'architect', 'ux', 'developer', 'qa', 'devops'], SuccessAdapter()
    )
    runtime = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)

    result = runtime.run(project_id, max_steps=10)
    assert result[-1]['state'] == 'done'
    assert len(runtime.runs(project_id)) == 6

    restarted = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    persisted_project = restarted.brain.get_project(project_id)
    persisted_tasks = restarted.brain.tasks(project_id)
    assert persisted_project is not None
    assert len(persisted_tasks) == len(tasks) == 6
    assert all(task['status'] == 'done' for task in persisted_tasks)
    assert all(run['status'] == 'succeeded' for run in restarted.runs(project_id))


def test_restart_recovery_interrupts_run_and_restores_task(runtime_workspace):
    runtime = PostgresRuntime(runtime_workspace, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)
    task_id = uuid.UUID(tasks[0]['id'])
    project_uuid = uuid.UUID(project_id)
    run_id = uuid.uuid4()

    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        current = uow.planning.get_task(task_id)
        uow.planning.update_task_status(task_id, 'running', expected_version=current.version)
        uow.planning.increment_attempts(task_id, expected_version=current.version + 1)
        uow.execution.create_task_run(
            tenant_id=TENANT_ID, task_id=task_id, project_id=project_uuid,
            agent_name=tasks[0]['agent'], run_id=run_id
        )
        uow.events.emit_event(
            'task.started', {'run_id': str(run_id)}, tenant_id=TENANT_ID,
            project_id=project_uuid, task_id=task_id
        )
        uow.commit()

    restarted = PostgresRuntime(runtime_workspace, tenant_id=TENANT_ID)
    assert restarted.recover(project_id) == 1
    recovered_task = next(t for t in restarted.brain.tasks(project_id) if t['id'] == str(task_id))
    recovered_run = next(r for r in restarted.runs(project_id) if r['id'] == str(run_id))
    assert recovered_task['status'] == 'pending'
    assert recovered_run['status'] == 'interrupted'
    recovery_events = [
        event for event in EventsRepository(tenant_id=TENANT_ID).execution_trace(project_uuid, task_id)
        if event.type == 'task.recovered'
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0].detail['run_id'] == str(run_id)
    assert recovery_events[0].tenant_id == TENANT_ID


def test_tenant_isolation_hides_another_tenants_project(runtime_workspace):
    tenants = IdentityRepository().list_tenants(active_only=True)
    other = next((tenant.id for tenant in tenants if tenant.id != TENANT_ID), None)
    if other is None:
        pytest.skip('Requires a second active tenant to verify isolation')

    owner = PostgresRuntime(runtime_workspace, tenant_id=TENANT_ID)
    project_id = owner.orchestrator.create_project(
        f'F4-001 Isolation {uuid.uuid4()}', 'Tenant isolation check'
    )
    observer = PostgresRuntime(runtime_workspace, tenant_id=other)
    assert observer.brain.get_project(project_id) is None
    assert observer.runs(project_id) == []


def test_start_transaction_rolls_back_task_run_and_event_together(runtime_workspace, monkeypatch):
    runtime = PostgresRuntime(runtime_workspace, {'pm': SuccessAdapter()}, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)

    def fail_event(*args, **kwargs):
        raise RuntimeError('simulated event write failure')

    monkeypatch.setattr(EventsRepository, 'emit_event', fail_event)
    with pytest.raises(RuntimeError, match='simulated event write failure'):
        runtime.step(project_id)

    first_task = next(task for task in runtime.brain.tasks(project_id) if task['id'] == tasks[0]['id'])
    assert first_task['status'] == 'pending'
    assert first_task['attempts'] == 0
    assert runtime.runs(project_id) == []
    rolled_back_types = {
        event.type for event in EventsRepository(tenant_id=TENANT_ID).execution_trace(
            uuid.UUID(project_id), uuid.UUID(tasks[0]['id'])
        )
    }
    assert not rolled_back_types.intersection({'task.claimed', 'task.started'})


def test_simultaneous_workers_claim_task_once(runtime_workspace):
    started = threading.Event()
    release = threading.Event()
    ready_barrier = threading.Barrier(2)

    class BlockingAdapter:
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def execute(self, task, project, context):
            with self.lock:
                self.calls += 1
            started.set()
            assert release.wait(timeout=15)
            return Result(True, 'Completed', ('evidence:verified',), approved=True)

    adapter = BlockingAdapter()
    project_creator = PostgresRuntime(runtime_workspace, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(project_creator)
    workers = [
        PostgresRuntime(runtime_workspace, {'pm': adapter}, tenant_id=TENANT_ID)
        for _ in range(2)
    ]
    for worker in workers:
        original_ready = worker.orchestrator.ready_tasks

        def synchronized_ready(pid, original=original_ready):
            result = original(pid)
            ready_barrier.wait(timeout=15)
            return result

        worker.orchestrator.ready_tasks = synchronized_ready

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker.step, project_id) for worker in workers]
        assert started.wait(timeout=15)
        release.set()
        outcomes = [future.result(timeout=30) for future in futures]

    first_task = next(task for task in workers[0].brain.tasks(project_id) if task['id'] == tasks[0]['id'])
    assert adapter.calls == 1
    assert first_task['attempts'] == 1
    assert first_task['status'] == 'done'
    assert len(workers[0].runs(project_id)) == 1
    assert sum(outcome.get('state') == 'done' for outcome in outcomes) == 1
    assert all(outcome.get('state') in {'done', 'concurrent_update', 'running'} for outcome in outcomes)


def test_restart_resumes_interrupted_task_and_keeps_audit_history(runtime_workspace):
    runtime = PostgresRuntime(runtime_workspace, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)
    task_id = uuid.UUID(tasks[0]['id'])
    project_uuid = uuid.UUID(project_id)
    interrupted_run_id = uuid.uuid4()

    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        current = uow.planning.get_task(task_id)
        claimed = uow.planning.claim_task_for_execution(
            task_id, project_uuid, expected_version=current.version
        )
        assert claimed is not None
        uow.execution.create_task_run(
            tenant_id=TENANT_ID, task_id=task_id, project_id=project_uuid,
            agent_name=tasks[0]['agent'], run_id=interrupted_run_id
        )
        uow.commit()

    class IdempotencyAwareAdapter:
        def __init__(self):
            self.keys = []

        def execute(self, task, project, context):
            self.keys.append(context['idempotency_key'])
            return Result(True, 'Recovered completion', ('evidence:verified',), approved=True)

    adapter = IdempotencyAwareAdapter()
    restarted = PostgresRuntime(runtime_workspace, {'pm': adapter}, tenant_id=TENANT_ID)
    result = restarted.run(project_id, max_steps=1)
    assert result[0]['state'] == 'done'
    assert adapter.keys == [tasks[0]['id']]
    task = next(t for t in restarted.brain.tasks(project_id) if t['id'] == tasks[0]['id'])
    assert task['status'] == 'done'
    runs = restarted.runs(project_id)
    assert next(run for run in runs if run['id'] == str(interrupted_run_id))['status'] == 'interrupted'
    assert sum(run['status'] == 'succeeded' for run in runs) == 1


def test_adapter_failures_retry_only_to_configured_attempt_limit(runtime_workspace):
    class FailingAdapter:
        def __init__(self):
            self.keys = []

        def execute(self, task, project, context):
            self.keys.append(context['idempotency_key'])
            return Result(False, 'temporary failure')

    adapter = FailingAdapter()
    adapters = {name: SuccessAdapter() for name in ['architect', 'ux', 'developer', 'qa', 'devops']}
    adapters['pm'] = adapter
    runtime = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)

    history = runtime.run(project_id, max_steps=10)
    first_task = next(t for t in runtime.brain.tasks(project_id) if t['id'] == tasks[0]['id'])
    task_runs = [run for run in runtime.runs(project_id) if run['task_id'] == tasks[0]['id']]
    assert first_task['status'] == 'failed'
    assert first_task['attempts'] == first_task['max_attempts'] == 3
    assert len(task_runs) == 3
    assert len(set(adapter.keys)) == 1
    assert any(item['state'] == 'failed' for item in history)


def test_completed_task_is_not_replayed_after_restart(runtime_workspace):
    class CountingAdapter(SuccessAdapter):
        def __init__(self):
            self.calls = 0

        def execute(self, task, project, context):
            self.calls += 1
            return super().execute(task, project, context)

    adapter = CountingAdapter()
    adapters = dict.fromkeys(['pm', 'architect', 'ux', 'developer', 'qa', 'devops'], adapter)
    runtime = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    project_id, _ = create_planned_project(runtime)
    runtime.run(project_id, max_steps=10)
    assert adapter.calls == 6

    restarted = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    assert restarted.run(project_id, max_steps=10) == [{'state': 'done', 'task': None}]
    assert adapter.calls == 6


def test_execution_trace_orders_and_correlates_lifecycle_events(runtime_workspace):
    runtime = PostgresRuntime(runtime_workspace, {'pm': SuccessAdapter()}, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)
    outcome = runtime.step(project_id)
    project_uuid = uuid.UUID(project_id)
    task_uuid = uuid.UUID(tasks[0]['id'])

    trace = EventsRepository(tenant_id=TENANT_ID).execution_trace(project_uuid, task_uuid)
    lifecycle = [
        event for event in trace
        if event.type in {'task.claimed', 'task.started', 'task.succeeded'}
    ]
    assert [event.type for event in lifecycle] == [
        'task.claimed', 'task.started', 'task.succeeded'
    ]
    run_id = outcome['run']
    assert all(
        event.tenant_id == TENANT_ID
        and event.project_id == project_uuid
        and event.task_id == task_uuid
        and event.detail['run_id'] == run_id
        for event in lifecycle
    )
    assert all(
        lifecycle[index].created_at <= lifecycle[index + 1].created_at
        for index in range(len(lifecycle) - 1)
    )


def test_retry_events_correlate_each_run_and_preserve_failure_history(runtime_workspace):
    class FailingAdapter:
        def execute(self, task, project, context):
            return Result(False, 'retryable failure')

    adapters = {name: SuccessAdapter() for name in ['architect', 'ux', 'developer', 'qa', 'devops']}
    adapters['pm'] = FailingAdapter()
    runtime = PostgresRuntime(runtime_workspace, adapters, tenant_id=TENANT_ID)
    project_id, tasks = create_planned_project(runtime)
    runtime.run(project_id, max_steps=2)

    trace = EventsRepository(tenant_id=TENANT_ID).execution_trace(
        uuid.UUID(project_id), uuid.UUID(tasks[0]['id'])
    )
    retries = [event for event in trace if event.type == 'task.retry']
    failures = [event for event in trace if event.type == 'task.failed']
    claims = [event for event in trace if event.type == 'task.claimed']
    assert len(retries) == len(failures) == len(claims) == 2
    assert [event.detail['run_id'] for event in retries] == [
        event.detail['run_id'] for event in failures
    ]
    assert len({event.detail['run_id'] for event in retries}) == 2
