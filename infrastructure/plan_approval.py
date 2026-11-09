"""Tenant-scoped plan approvals built on the existing durable event ledger."""
from __future__ import annotations

import hashlib
import json
import uuid


class PlanApprovalError(ValueError):
    """Raised when a project plan is not explicitly approved for execution."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      default=lambda item: str(item) if isinstance(item, uuid.UUID) else item.isoformat())


def _stable_odd(body):
    """Keep ODD plan content while excluding mutable execution/history fields."""
    if body is None:
        return None
    try:
        document = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError):
        return body
    if not isinstance(document, dict):
        return document
    return {
        'project_id': document.get('project_id'),
        'name': document.get('name'),
        'request': document.get('request'),
        'specs': [
            {key: spec.get(key) for key in ('id', 'title', 'description', 'acceptance')}
            for spec in document.get('specs', []) if isinstance(spec, dict)
        ],
        'tasks': [
            {key: task.get(key) for key in ('id', 'spec_id', 'route', 'description')}
            for task in document.get('tasks', []) if isinstance(task, dict)
        ],
    }


def plan_fingerprint(conn, tenant_id, project_id):
    """Hash the immutable plan definition, excluding execution lifecycle fields."""
    tenant_id, project_id = str(tenant_id), str(project_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, request FROM projects WHERE id = %s AND tenant_id = %s",
            (project_id, tenant_id),
        )
        project = cur.fetchone()
        if project is None:
            raise KeyError('Project not found in this tenant')
        cur.execute("SELECT body FROM odd_documents WHERE project_id = %s", (project_id,))
        odd = cur.fetchone()
        cur.execute("SELECT spec, spec_hash FROM project_specs WHERE project_id = %s", (project_id,))
        spec = cur.fetchone()
        cur.execute(
            """SELECT id, title, description, agent, depends_on, max_attempts,
                      timeout_seconds, priority, scheduled_at
                 FROM tasks WHERE tenant_id = %s AND project_id = %s ORDER BY id""",
            (tenant_id, project_id),
        )
        tasks = cur.fetchall()
    if not tasks:
        raise PlanApprovalError('Cannot approve a project without a persisted plan')
    payload = {
        'tenant_id': tenant_id,
        'project_id': str(project[0]),
        'name': project[1],
        'request': project[2],
        'odd_document': _stable_odd(odd[0]) if odd else None,
        'project_spec': spec[0] if spec else None,
        'project_spec_hash': spec[1] if spec else None,
        'tasks': [
            {
                'id': str(row[0]), 'title': row[1], 'description': row[2],
                'agent': row[3], 'depends_on': sorted(str(item) for item in (row[4] or [])),
                'max_attempts': row[5], 'timeout_seconds': row[6],
                'priority': row[7], 'scheduled_at': row[8],
            }
            for row in tasks
        ],
    }
    return hashlib.sha256(_canonical(payload).encode('utf-8')).hexdigest()


def _lock_project(conn, tenant_id, project_id):
    with conn.cursor() as cur:
        cur.execute(
            'SELECT id FROM projects WHERE id = %s AND tenant_id = %s FOR UPDATE',
            (str(project_id), str(tenant_id)),
        )
        if cur.fetchone() is None:
            raise KeyError('Project not found in this tenant')


def approval_status(conn, tenant_id, project_id, *, lock=False):
    if lock:
        _lock_project(conn, tenant_id, project_id)
    try:
        fingerprint = plan_fingerprint(conn, tenant_id, project_id)
    except PlanApprovalError:
        return {'approved': False, 'plan_hash': None}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT type, detail FROM events
                 WHERE tenant_id = %s AND project_id = %s
                   AND type IN ('plan.approved', 'plan.approval.revoked')
                 ORDER BY created_at DESC, id DESC""",
            (str(tenant_id), str(project_id)),
        )
        approvals = cur.fetchall()
    for event_type, detail in approvals:
        if (isinstance(detail, dict) and detail.get('plan_hash') == fingerprint):
            return {'approved': event_type == 'plan.approved', 'plan_hash': fingerprint}
    return {'approved': False, 'plan_hash': fingerprint}


def record_plan_decision(uow, project_id, *, actor_id, expected_hash, approve):
    """Append an explicit approval/revocation after locking and rechecking the plan."""
    conn = uow._get_connection()
    _lock_project(conn, uow.tenant_id, project_id)
    current_hash = plan_fingerprint(conn, uow.tenant_id, project_id)
    if not expected_hash or expected_hash != current_hash:
        raise PlanApprovalError('Plan changed; review its current fingerprint before approval')
    event_type = 'plan.approved' if approve else 'plan.approval.revoked'
    event = uow.events.emit_event(
        event_type,
        {'plan_hash': current_hash, 'actor_id': str(actor_id), 'approval_version': 1},
        tenant_id=uow.tenant_id, project_id=project_id,
    )
    uow.commit()
    return {'approved': approve, 'plan_hash': current_hash, 'event_id': str(event.id)}


def require_plan_approval(conn, tenant_id, project_id):
    state = approval_status(conn, tenant_id, project_id, lock=True)
    if not state['approved']:
        raise PlanApprovalError(
            'Explicit approval for the current plan is required before task execution '
            f'(plan_hash={state["plan_hash"] or "unplanned"})'
        )
    return state['plan_hash']
