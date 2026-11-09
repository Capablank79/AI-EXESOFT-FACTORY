"""Authenticated Factory API integration/security tests with local PostgreSQL."""
import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import psycopg

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

from api.app import create_app
from engine.runtime import Result
from infrastructure.repositories import IdentityRepository, ProjectsRepository
from infrastructure.repositories.quality import QualityRepository
from quality.evidence import record_evidence

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')


def create_user(role='developer', tenant_id=TENANT_ID):
    return IdentityRepository().create_user(
        tenant_id, f'api-{uuid.uuid4()}@example.invalid', role=role
    )


def token_for(user):
    token = f'api-test-token-{uuid.uuid4()}'
    digest = hashlib.sha256(token.encode()).hexdigest()
    return token, {digest: str(user.id)}


def asgi_request(app, method, path, *, token=None, body=None):
    payload = b'' if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    headers = [(b'host', b'testserver')]
    if body is not None:
        headers.append((b'content-type', b'application/json'))
        headers.append((b'content-length', str(len(payload)).encode()))
    if token:
        headers.append((b'authorization', f'Bearer {token}'.encode()))
    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
        'query_string': b'', 'root_path': '', 'headers': headers,
        'client': ('127.0.0.1', 12345), 'server': ('testserver', 80),
    }
    sent = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {'type': 'http.disconnect'}
        received = True
        return {'type': 'http.request', 'body': payload, 'more_body': False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(message for message in sent if message['type'] == 'http.response.start')
    response_body = b''.join(message.get('body', b'') for message in sent
                             if message['type'] == 'http.response.body')
    return start['status'], json.loads(response_body) if response_body else None


def test_api_authentication_validation_and_role_authorization(tmp_path, monkeypatch):
    viewer = create_user('viewer')
    token, mapping = token_for(viewer)
    app = create_app(tmp_path, token_users=mapping)

    status, response = asgi_request(app, 'GET', '/projects')
    assert status == 401 and response['error']['code'] == 'unauthorized'
    status, response = asgi_request(app, 'GET', '/projects', token='bad-token')
    assert status == 401 and response['error']['code'] == 'unauthorized'
    status, response = asgi_request(app, 'POST', '/projects', token=token,
                                    body={'name': 'No permission', 'request': 'Denied'})
    assert status == 403 and response['error']['code'] == 'forbidden'
    status, response = asgi_request(app, 'POST', '/projects', token=token,
                                    body={'name': '', 'request': 'invalid'})
    assert status == 403
    status, response = asgi_request(app, 'GET', '/health')
    assert status == 200 and response == {
        'status': 'ok', 'checks': {'postgresql': 'ok'}
    }
    status, response = asgi_request(app, 'DELETE', '/projects', token=token)
    assert status == 405 and response['error']['code'] == 'method_not_allowed'

    def database_failure(*args, **kwargs):
        raise psycopg.OperationalError('password=must-not-be-returned')

    monkeypatch.setattr(ProjectsRepository, 'list_projects', database_failure)
    status, response = asgi_request(app, 'GET', '/projects', token=token)
    assert status == 503
    assert 'must-not-be-returned' not in json.dumps(response)


def test_api_project_task_and_run_operations_use_postgres_and_quality_gate(tmp_path, monkeypatch):
    import api.app as api_module
    from engine.postgres_runtime import PostgresRuntime
    from plan_approval_test_support import install_mock_planner

    install_mock_planner(monkeypatch)

    user = create_user('admin')
    token, mapping = token_for(user)
    app = create_app(tmp_path, token_users=mapping)

    class SandyResultAdapter:
        def execute(self, task, project, context):
            if task['agent'] not in {'developer', 'qa'}:
                return Result(True, 'Completed')
            workspace = Path(context['workspace'])
            workspace.mkdir(parents=True, exist_ok=True)
            output = 'Ran 1 test'
            reference = record_evidence(workspace, {
                'schema': 'exesoft.quality.test.v1', 'id': uuid.uuid4().hex,
                'kind': 'test', 'runner': 'sandy', 'status': 'passed',
                'returncode': 0, 'sandy_exit_code': 0, 'output': output,
                'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
                'tests_run': 1, 'result_sha256': 'd' * 64, 'partial_ok': False,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            })
            return Result(True, 'Sandy tests passed', (reference,),
                          approved=task['agent'] == 'qa')

    monkeypatch.setattr(
        api_module, 'AgentRuntime',
        lambda root, tenant_id: PostgresRuntime(
            root, adapters={role: SandyResultAdapter() for role in
                            ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')},
            tenant_id=tenant_id
        )
    )

    status, response = asgi_request(app, 'POST', '/projects', token=token,
                                    body={'name': 'API Project', 'request': 'Validate API'})
    assert status == 201
    project_id = response['project']['id']

    status, response = asgi_request(app, 'POST', f'/projects/{project_id}/plan', token=token)
    assert status == 201 and len(response['tasks']) == 6
    status, approval = asgi_request(app, 'GET', f'/projects/{project_id}/approval', token=token)
    assert status == 200 and approval['approved'] is False
    status, approval = asgi_request(
        app, 'POST', f'/projects/{project_id}/approval', token=token,
        body={'plan_hash': approval['plan_hash'], 'approved': True},
    )
    assert status == 201 and approval['approved'] is True
    status, response = asgi_request(app, 'GET', f'/projects/{project_id}', token=token)
    assert status == 200 and response['project']['tenant_id'] == str(TENANT_ID)
    task_id = response['tasks'][0]['id']
    status, response = asgi_request(
        app, 'GET', f'/projects/{project_id}/tasks/{task_id}', token=token
    )
    assert status == 200 and response['task']['id'] == task_id

    status, response = asgi_request(app, 'POST', f'/projects/{project_id}/run',
                                    token=token, body={'max_steps': 10})
    assert status == 200 and response['status'] == 'done'
    assert all(run['status'] == 'succeeded' for run in response['runs'])
    decisions = QualityRepository(tenant_id=TENANT_ID).list_validations(
        uuid.UUID(project_id), limit=100
    )
    assert any(item.status == 'passed' and item.metrics['gate'] == 'f6_execution'
               for item in decisions)
    status, response = asgi_request(app, 'GET', f'/projects/{project_id}/runs', token=token)
    assert status == 200 and len(response['runs']) == 6
    status, task_list = asgi_request(app, 'GET', f'/projects/{project_id}/tasks', token=token)
    status, created_task = asgi_request(
        app, 'POST', f'/projects/{project_id}/tasks', token=token,
        body={'title': 'Follow-up task', 'agent': 'developer',
              'depends_on': [task_list['tasks'][-1]['id']]}
    )
    assert status == 201
    assert created_task['task']['title'] == 'Follow-up task'


def test_api_tenant_isolation_and_request_error_schemas(tmp_path):
    owner = create_user('developer', TENANT_ID)
    outsider_tenant = IdentityRepository().create_tenant(
        f'API outsider {uuid.uuid4()}', f'api-outsider-{uuid.uuid4()}'
    )
    outsider = create_user('viewer', outsider_tenant.id)
    owner_token, owner_mapping = token_for(owner)
    outsider_token, outsider_mapping = token_for(outsider)
    app = create_app(tmp_path, token_users={**owner_mapping, **outsider_mapping})

    status, response = asgi_request(app, 'POST', '/projects', token=owner_token,
                                    body={'name': 'Private project', 'request': 'Private'})
    assert status == 201
    project_id = response['project']['id']

    status, response = asgi_request(app, 'GET', f'/projects/{project_id}', token=outsider_token)
    assert status == 404 and response['error']['code'] == 'not_found'
    status, response = asgi_request(app, 'POST', f'/projects/{project_id}/run',
                                    token=outsider_token, body={'max_steps': 1})
    assert status == 404
    status, response = asgi_request(app, 'POST', '/projects', token=owner_token,
                                    body={'name': 'bad', 'request': 'x', 'tenant_id': str(uuid.uuid4())})
    assert status == 422 and response['error']['code'] == 'invalid_request'
    status, response = asgi_request(app, 'GET', '/projects/not-a-uuid', token=owner_token)
    assert status == 422 and response['error']['code'] == 'invalid_id'
    status, response = asgi_request(app, 'POST', '/projects', token=owner_token, body=b'{invalid')
    assert status == 400 and response['error']['code'] == 'invalid_json'


def test_api_execution_cannot_bypass_quality_gate(tmp_path, monkeypatch):
    import api.app as api_module
    from engine.postgres_runtime import PostgresRuntime
    from plan_approval_test_support import install_mock_planner

    install_mock_planner(monkeypatch)

    user = create_user('admin')
    token, mapping = token_for(user)
    app = create_app(tmp_path, token_users=mapping)

    class UnverifiedAdapter:
        def execute(self, task, project, context):
            return Result(True, 'Claimed success without sandbox evidence', (), approved=True)

    monkeypatch.setattr(
        api_module, 'AgentRuntime',
        lambda root, tenant_id: PostgresRuntime(
            root, adapters={role: UnverifiedAdapter() for role in
                            ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')},
            tenant_id=tenant_id
        )
    )
    status, response = asgi_request(app, 'POST', '/projects', token=token,
                                    body={'name': 'Gate API', 'request': 'Reject missing evidence'})
    assert status == 201
    project_id = response['project']['id']
    status, _ = asgi_request(app, 'POST', f'/projects/{project_id}/plan', token=token)
    assert status == 201
    status, approval = asgi_request(app, 'GET', f'/projects/{project_id}/approval', token=token)
    assert status == 200
    status, approval = asgi_request(
        app, 'POST', f'/projects/{project_id}/approval', token=token,
        body={'plan_hash': approval['plan_hash'], 'approved': True},
    )
    assert status == 201 and approval['approved'] is True
    status, response = asgi_request(app, 'POST', f'/projects/{project_id}/run',
                                    token=token, body={'max_steps': 10})
    assert status == 200 and response['status'] == 'blocked'
    assert all(run['status'] != 'succeeded' for run in response['runs']
               if run['agent'] == 'developer')
    developer_attempts = [item for item in response['history']
                          if item.get('agent') == 'developer']
    assert developer_attempts[-1]['state'] == 'failed'
    validations = QualityRepository(tenant_id=TENANT_ID).list_validations(
        uuid.UUID(project_id), limit=100
    )
    assert any(item.status == 'failed' and not item.metrics['approved']
               for item in validations)
