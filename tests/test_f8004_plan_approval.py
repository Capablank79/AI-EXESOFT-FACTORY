"""Plan approval gates: isolated PostgreSQL only, using synthetic projects."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time
import uuid

import pytest

from engine.postgres_runtime import PostgresRuntime
from engine.runtime import Result
from infrastructure.plan_approval import (
    PlanApprovalError, approval_status, record_plan_decision,
)
from infrastructure.repositories.events import EventsRepository
from infrastructure.uow import UnitOfWork

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


def _project(root, isolated_postgres, adapter=None):
    runtime = PostgresRuntime(root, {'pm': adapter} if adapter else {}, tenant_id=TENANT_ID)
    project_id = runtime.orchestrator.create_project(
        f'T1 synthetic approval {uuid.uuid4()}', 'Test plan approval enforcement only',
    )
    isolated_postgres(project_id)
    runtime.orchestrator.plan_skeleton(project_id)
    return runtime, project_id


def _approval_state(runtime, project_id):
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        return approval_status(
            uow._get_connection(), TENANT_ID, uuid.UUID(project_id),
        )


def _decide(runtime, project_id, approved, expected_hash=None):
    state = _approval_state(runtime, project_id)
    with UnitOfWork(tenant_id=TENANT_ID) as uow:
        return record_plan_decision(
            uow, uuid.UUID(project_id), actor_id='synthetic-test-operator',
            expected_hash=expected_hash or state['plan_hash'], approve=approved,
        )


def test_unapproved_plan_is_rejected_before_claim_or_dispatch(tmp_path, isolated_postgres):
    calls = []

    class Adapter:
        def execute(self, task, project, context):
            calls.append(task['id'])
            return Result(True, 'must not execute')

    runtime, project_id = _project(tmp_path, isolated_postgres, Adapter())
    with pytest.raises(PlanApprovalError, match='Explicit approval'):
        runtime.run(project_id)
    assert calls == []
    assert all(task['status'] == 'pending' for task in runtime.brain.tasks(project_id))
    assert runtime.runs(project_id) == []


def test_approved_current_plan_executes_and_records_approval(tmp_path, isolated_postgres):
    class Adapter:
        def execute(self, task, project, context):
            return Result(True, 'approved execution')

    runtime, project_id = _project(tmp_path, isolated_postgres, Adapter())
    approved = _decide(runtime, project_id, True)
    assert approved['approved'] is True
    assert approved['plan_hash'] == _approval_state(runtime, project_id)['plan_hash']
    result = runtime.step(project_id)
    assert result['state'] == 'done'
    events = EventsRepository(tenant_id=TENANT_ID).execution_trace(uuid.UUID(project_id))
    decisions = [event for event in events if event.type == 'plan.approved']
    assert len(decisions) == 1
    assert decisions[0].detail['plan_hash'] == approved['plan_hash']


def test_plan_change_and_revocation_invalidate_approval(tmp_path, isolated_postgres):
    runtime, project_id = _project(tmp_path, isolated_postgres)
    original = _decide(runtime, project_id, True)['plan_hash']
    runtime.brain.add_task(str(uuid.uuid4()), project_id, 'Changed plan task', 'pm')
    assert _approval_state(runtime, project_id)['plan_hash'] != original
    with pytest.raises(PlanApprovalError):
        runtime.step(project_id)

    # The revised plan can be explicitly approved, then revoked as a durable event.
    _decide(runtime, project_id, True)
    _decide(runtime, project_id, False)
    assert _approval_state(runtime, project_id)['approved'] is False
    with pytest.raises(PlanApprovalError):
        runtime.step(project_id)


def test_cross_tenant_cannot_read_or_execute_another_tenants_plan(tmp_path, isolated_postgres):
    runtime, project_id = _project(tmp_path, isolated_postgres)
    _decide(runtime, project_id, True)
    foreign = PostgresRuntime(tmp_path, tenant_id=uuid.uuid4())
    with pytest.raises(KeyError):
        foreign.run(project_id)


def test_concurrent_execution_claims_only_once_after_approval(tmp_path, isolated_postgres):
    started = threading.Event()
    calls = []
    calls_lock = threading.Lock()

    class Adapter:
        def execute(self, task, project, context):
            with calls_lock:
                calls.append(task['id'])
            started.set()
            time.sleep(0.2)
            return Result(True, 'one concurrent dispatch')

    runtime, project_id = _project(tmp_path, isolated_postgres, Adapter())
    _decide(runtime, project_id, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runtime.step, project_id) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert started.is_set()
    assert len(calls) == 1
    assert len(runtime.runs(project_id)) == 1
    assert sum(item['state'] == 'done' for item in results) == 1
