"""Authenticated HTTP API backed by the PostgreSQL Factory Runtime.

Configure ``EXESOFT_API_TOKEN_USERS`` as a JSON object mapping SHA-256 bearer
token digests to PostgreSQL user UUIDs. Raw bearer tokens are never persisted
or logged. Tenant and role are always read from the active user record.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
import asyncio
import logging
import time
from datetime import date, datetime
from dataclasses import asdict, is_dataclass
from pathlib import Path

import psycopg
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from infrastructure import db
from infrastructure.observability import bind_log_context, log_event, reset_log_context

_logger = logging.getLogger('factory.api')

from engine.agent_runtime import AgentRuntime
from engine.orchestrator import (
    Orchestrator, PlanConflictError, PlanGatewayError, PlanValidationError,
)
from engine.postgres_runtime import PostgresRuntime
from infrastructure.repositories import IdentityRepository, ProjectsRepository
from infrastructure.uow import UnitOfWork
from infrastructure.plan_approval import PlanApprovalError
from engine.orchestrator import ROLES


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


class ApiAuthenticator:
    """Resolve a configured token digest to an active DB user."""

    def __init__(self, token_users=None):
        if token_users is None:
            try:
                token_users = json.loads(os.environ.get('EXESOFT_API_TOKEN_USERS', '{}'))
            except (TypeError, ValueError) as exc:
                raise ValueError('EXESOFT_API_TOKEN_USERS must be a JSON object') from exc
        if not isinstance(token_users, dict):
            raise ValueError('EXESOFT_API_TOKEN_USERS must be a JSON object')
        self.token_users = {}
        for digest, user_id in token_users.items():
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in '0123456789abcdef' for char in digest.lower())):
                raise ValueError('API token keys must be SHA-256 hex digests')
            self.token_users[digest.lower()] = uuid.UUID(str(user_id))

    def authenticate(self, authorization):
        if not isinstance(authorization, str):
            raise ApiError(401, 'unauthorized', 'A bearer token is required')
        scheme, separator, token = authorization.partition(' ')
        if not separator or scheme.lower() != 'bearer' or not token.strip():
            raise ApiError(401, 'unauthorized', 'A bearer token is required')
        digest = hashlib.sha256(token.strip().encode('utf-8')).hexdigest()
        user_id = next((candidate for expected, candidate in self.token_users.items()
                        if hmac.compare_digest(digest, expected)), None)
        if user_id is None:
            raise ApiError(401, 'unauthorized', 'Invalid bearer token')
        user = IdentityRepository().get_user(user_id)
        if user is None or not user.is_active:
            raise ApiError(401, 'unauthorized', 'Invalid bearer token')
        tenant = IdentityRepository().get_tenant(user.tenant_id)
        if tenant is None or not tenant.is_active:
            raise ApiError(401, 'unauthorized', 'Invalid bearer token')
        if user.role not in {'admin', 'developer', 'qa', 'viewer'}:
            raise ApiError(403, 'forbidden', 'User role is not authorized for API access')
        return user


READ_ROLES = {'admin', 'developer', 'qa', 'viewer'}
WRITE_ROLES = {'admin', 'developer'}
EXECUTION_ROLES = {'admin', 'developer', 'qa'}


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def _body(request: Request, allowed_fields):
    content_length = request.headers.get('content-length')
    if content_length:
        try:
            if int(content_length) > 65536:
                raise ApiError(413, 'request_too_large', 'Request body exceeds 65536 bytes')
        except ValueError:
            raise ApiError(400, 'invalid_request', 'Content-Length is invalid')
    try:
        payload = await request.body()
        if len(payload) > 65536:
            raise ApiError(413, 'request_too_large', 'Request body exceeds 65536 bytes')
        data = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        raise ApiError(400, 'invalid_json', 'Request body must contain valid JSON')
    if not isinstance(data, dict):
        raise ApiError(422, 'invalid_request', 'Request body must be a JSON object')
    unknown = set(data) - set(allowed_fields)
    if unknown:
        raise ApiError(422, 'invalid_request', 'Request contains unsupported fields')
    return data


def _uuid(value, field):
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ApiError(422, 'invalid_id', f'{field} must be a UUID')


def _runtime(request, principal, *, execution=False):
    cls = AgentRuntime if execution else PostgresRuntime
    return cls(request.app.state.root, tenant_id=principal.tenant_id)


def _project_or_404(runtime, project_id):
    project_id = _uuid(project_id, 'project_id')
    project = runtime.brain.get_project(str(project_id))
    if project is None or project.tenant_id != runtime.tenant_id:
        raise ApiError(404, 'not_found', 'Project not found')
    return project_id, project


def _require(principal, roles):
    if principal.role not in roles:
        raise ApiError(403, 'forbidden', 'Insufficient role for this operation')


async def health(request):
    healthy = db.health_check()
    status = 'ok' if healthy else 'degraded'
    http_status = 200 if healthy else 503
    log_event(_logger, logging.INFO if healthy else logging.ERROR, 'api.health',
              component='api', check='postgresql', outcome=status, http_status=http_status)
    return JSONResponse({'status': status, 'checks': {'postgresql': 'ok' if healthy else 'unavailable'}},
                        status_code=http_status)


async def list_projects(request):
    principal = request.state.principal
    _require(principal, READ_ROLES)
    try:
        limit = int(request.query_params.get('limit', '100'))
    except ValueError:
        raise ApiError(422, 'invalid_request', 'limit must be an integer')
    if not 1 <= limit <= 500:
        raise ApiError(422, 'invalid_request', 'limit must be between 1 and 500')
    projects = ProjectsRepository(tenant_id=principal.tenant_id).list_projects(
        principal.tenant_id, limit=limit
    )
    return JSONResponse({'projects': _jsonable(projects)})


async def create_project(request):
    principal = request.state.principal
    _require(principal, WRITE_ROLES)
    data = await _body(request, {'name', 'request'})
    name, description = data.get('name'), data.get('request')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ApiError(422, 'invalid_request', 'name must be a non-empty string of at most 200 characters')
    if not isinstance(description, str) or not description.strip() or len(description) > 20000:
        raise ApiError(422, 'invalid_request', 'request must be a non-empty string of at most 20000 characters')
    orchestrator = Orchestrator(
        request.app.state.root, tenant_id=principal.tenant_id, backend='postgres'
    )
    project_id = orchestrator.create_project(name.strip(), description.strip())
    project = PostgresRuntime(request.app.state.root, tenant_id=principal.tenant_id)._project(project_id)
    return JSONResponse({'project': _jsonable(project)}, status_code=201)


async def get_project(request):
    principal = request.state.principal
    _require(principal, READ_ROLES)
    runtime = _runtime(request, principal)
    project_id, project = _project_or_404(runtime, request.path_params['project_id'])
    tasks = runtime.brain.tasks(str(project_id))
    return JSONResponse({'project': _jsonable(project), 'tasks': _jsonable(tasks)})


async def plan_project(request):
    principal = request.state.principal
    runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(runtime, request.path_params['project_id'])
    _require(principal, WRITE_ROLES)
    mode = request.query_params.get('mode', 'requirements')
    if mode not in {'requirements', 'legacy_skeleton'}:
        raise ApiError(422, 'invalid_request', 'mode must be requirements or legacy_skeleton')
    try:
        if mode == 'legacy_skeleton':
            tasks = runtime.orchestrator.plan_skeleton(str(project_id))
            result = {'tasks': tasks, 'planner': 'legacy_skeleton'}
        else:
            replan_hash = request.query_params.get('replan_authorized_hash')
            if replan_hash:
                result = runtime.orchestrator.plan_from_requirements(
                    str(project_id), replan_authorized_hash=replan_hash,
                )
            else:
                result = runtime.orchestrator.plan_from_requirements(str(project_id))
            result['planner'] = 'requirements_gateway'
    except PlanValidationError as exc:
        raise ApiError(422, 'invalid_plan', str(exc))
    except PlanGatewayError:
        raise ApiError(503, 'planner_unavailable', 'Configured Model Gateway could not produce a plan')
    except PlanConflictError as exc:
        raise ApiError(409, 'conflict', str(exc))
    except ValueError as exc:
        raise ApiError(409, 'conflict', str(exc))
    return JSONResponse(_jsonable(result), status_code=201)


async def plan_approval(request):
    """Read or explicitly record a tenant-scoped approval for the current plan."""
    principal = request.state.principal
    runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(runtime, request.path_params['project_id'])
    if request.method == 'GET':
        _require(principal, READ_ROLES)
        from infrastructure.plan_approval import approval_status
        with UnitOfWork(tenant_id=principal.tenant_id) as uow:
            state = approval_status(
                uow._get_connection(), principal.tenant_id, project_id,
            )
        return JSONResponse(state)

    # Plan approval is an operator decision, reserved to tenant administrators.
    _require(principal, {'admin'})
    data = await _body(request, {'plan_hash', 'approved'})
    plan_hash, approved = data.get('plan_hash'), data.get('approved')
    if (not isinstance(plan_hash, str) or len(plan_hash) != 64
            or any(char not in '0123456789abcdef' for char in plan_hash)):
        raise ApiError(422, 'invalid_request', 'plan_hash must be a lowercase SHA-256 fingerprint')
    if type(approved) is not bool:
        raise ApiError(422, 'invalid_request', 'approved must be a boolean')
    from infrastructure.plan_approval import PlanApprovalError, record_plan_decision
    try:
        with UnitOfWork(tenant_id=principal.tenant_id) as uow:
            result = record_plan_decision(
                uow, project_id, actor_id=principal.id,
                expected_hash=plan_hash, approve=approved,
            )
    except PlanApprovalError as exc:
        raise ApiError(409, 'plan_changed', str(exc))
    return JSONResponse(result, status_code=201)


async def create_task(request):
    principal = request.state.principal
    runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(runtime, request.path_params['project_id'])
    _require(principal, WRITE_ROLES)
    data = await _body(request, {
        'title', 'agent', 'description', 'depends_on', 'max_attempts', 'priority'
    })
    title, agent = data.get('title'), data.get('agent')
    description = data.get('description')
    max_attempts = data.get('max_attempts', 3)
    priority = data.get('priority', 5)
    dependencies = data.get('depends_on', [])
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ApiError(422, 'invalid_request', 'title must be a non-empty string of at most 300 characters')
    if not isinstance(agent, str) or agent not in ROLES:
        raise ApiError(422, 'invalid_request', 'agent is not a supported Factory role')
    if description is not None and (not isinstance(description, str) or len(description) > 20000):
        raise ApiError(422, 'invalid_request', 'description must be a string of at most 20000 characters')
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ApiError(422, 'invalid_request', 'max_attempts must be an integer between 1 and 10')
    if type(priority) is not int or not 1 <= priority <= 10:
        raise ApiError(422, 'invalid_request', 'priority must be an integer between 1 and 10')
    if not isinstance(dependencies, list) or len(dependencies) > 100:
        raise ApiError(422, 'invalid_request', 'depends_on must be a list of at most 100 task UUIDs')
    dependency_ids = [_uuid(value, 'depends_on') for value in dependencies]
    if len(set(dependency_ids)) != len(dependency_ids):
        raise ApiError(422, 'invalid_request', 'depends_on cannot contain duplicate task IDs')
    project_tasks = runtime.brain.tasks(str(project_id))
    project_task_ids = {item['id'] for item in project_tasks}
    if any(str(value) not in project_task_ids for value in dependency_ids):
        raise ApiError(422, 'invalid_request', 'Every dependency must belong to this project')
    with UnitOfWork(tenant_id=principal.tenant_id) as uow:
        task = uow.planning.create_task(
            tenant_id=principal.tenant_id, project_id=project_id,
            title=title.strip(), description=description, agent=agent,
            depends_on=[str(value) for value in dependency_ids],
            priority=priority, max_attempts=max_attempts,
            created_by=principal.id,
        )
        uow.events.emit_event(
            'task.created', {'title': task.title, 'agent': task.agent},
            tenant_id=principal.tenant_id, project_id=project_id, task_id=task.id
        )
        uow.commit()
    return JSONResponse({'task': _jsonable(task)}, status_code=201)


async def list_tasks(request):
    principal = request.state.principal
    _require(principal, READ_ROLES)
    runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(runtime, request.path_params['project_id'])
    return JSONResponse({'tasks': _jsonable(runtime.brain.tasks(str(project_id)))})


async def get_task(request):
    principal = request.state.principal
    _require(principal, READ_ROLES)
    runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(runtime, request.path_params['project_id'])
    task_id = _uuid(request.path_params['task_id'], 'task_id')
    task = next((item for item in runtime.brain.tasks(str(project_id))
                 if item['id'] == str(task_id)), None)
    if task is None:
        raise ApiError(404, 'not_found', 'Task not found')
    return JSONResponse({'task': _jsonable(task)})


async def list_runs(request):
    principal = request.state.principal
    _require(principal, READ_ROLES)
    runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(runtime, request.path_params['project_id'])
    return JSONResponse({'runs': _jsonable(runtime.runs(str(project_id)))})


async def execute_project(request):
    principal = request.state.principal
    data_runtime = _runtime(request, principal)
    project_id, _ = _project_or_404(data_runtime, request.path_params['project_id'])
    _require(principal, EXECUTION_ROLES)
    runtime = _runtime(request, principal, execution=True)
    if (request.headers.get('content-length', '0') not in {'', '0'}
            or request.headers.get('transfer-encoding')):
        data = await _body(request, {'max_steps'})
    else:
        data = {}
    max_steps = data.get('max_steps', 20)
    if type(max_steps) is not int or not 1 <= max_steps <= 100:
        raise ApiError(422, 'invalid_request', 'max_steps must be an integer between 1 and 100')
    try:
        history = await asyncio.to_thread(runtime.run, str(project_id), max_steps=max_steps)
        project = runtime._project(str(project_id))
    except PlanApprovalError as exc:
        raise ApiError(409, 'plan_approval_required', str(exc))
    except KeyError:
        raise ApiError(404, 'not_found', 'Project not found')
    return JSONResponse({
        'project_id': str(project_id), 'status': project['status'],
        'history': _jsonable(history), 'runs': _jsonable(runtime.runs(str(project_id))),
    })


async def add_authentication(request, call_next):
    supplied_id = request.headers.get('x-request-id', '')
    try:
        request_id = str(uuid.UUID(supplied_id))
    except (ValueError, AttributeError):
        request_id = str(uuid.uuid4())
    parts = request.url.path.strip('/').split('/')
    route = '/unmatched'
    correlations = {}
    if request.url.path == '/health':
        route = '/health'
    elif len(parts) >= 2 and parts[0] == 'projects':
        route = '/projects/{project_id}'
        try:
            correlations['project_id'] = str(uuid.UUID(parts[1]))
        except ValueError:
            pass
        if len(parts) == 3 and parts[2] in {'plan', 'tasks', 'runs', 'run', 'resume'}:
            route += '/' + parts[2]
        elif len(parts) == 4 and parts[2] == 'tasks':
            route += '/tasks/{task_id}'
            try:
                correlations['task_id'] = str(uuid.UUID(parts[3]))
            except ValueError:
                pass
    elif request.url.path == '/projects':
        route = '/projects'
    token = bind_log_context(request_id=request_id, http_method=request.method,
                             http_route=route, **correlations)
    started = time.monotonic()
    response = None
    try:
        if request.url.path == '/health':
            response = await call_next(request)
        else:
            try:
                request.state.principal = request.app.state.authenticator.authenticate(
                    request.headers.get('authorization')
                )
                bind_log_context(tenant_id=str(request.state.principal.tenant_id))
            except ApiError as exc:
                log_event(_logger, logging.WARNING, 'api.authentication.rejected', component='api',
                          outcome=exc.code, http_status=exc.status_code)
                response = JSONResponse({'error': {'code': exc.code, 'message': exc.message}},
                                        status_code=exc.status_code,
                                        headers={'WWW-Authenticate': 'Bearer'} if exc.status_code == 401 else None)
            except psycopg.Error as exc:
                log_event(_logger, logging.ERROR, 'api.authentication.failed', component='api',
                          error_type=type(exc).__name__, http_status=503)
                response = JSONResponse({'error': {'code': 'database_unavailable',
                                                   'message': 'Database operation failed'}}, status_code=503)
            except Exception as exc:
                log_event(_logger, logging.ERROR, 'api.authentication.failed', component='api',
                          error_type=type(exc).__name__, http_status=503)
                response = JSONResponse({'error': {'code': 'authentication_unavailable',
                                                   'message': 'Authentication could not be completed'}}, status_code=503)
            if response is None:
                response = await call_next(request)
        return response
    finally:
        try:
            status = response.status_code if response is not None else 500
            log_event(_logger, logging.INFO if status < 500 else logging.ERROR,
                      'api.request.completed', component='api', http_status=status,
                      duration_ms=int((time.monotonic() - started) * 1000))
            if response is not None:
                response.headers['X-Request-ID'] = request_id
        finally:
            reset_log_context(token)


async def api_error_handler(request, exc):
    return JSONResponse({'error': {'code': exc.code, 'message': exc.message}},
                        status_code=exc.status_code)


async def database_error_handler(request, exc):
    # Do not return driver messages, SQL, or connection configuration to callers.
    return JSONResponse({'error': {'code': 'database_unavailable',
                                   'message': 'Database operation failed'}}, status_code=503)


async def unexpected_error_handler(request, exc):
    return JSONResponse({'error': {'code': 'internal_error',
                                   'message': 'Request could not be completed'}}, status_code=500)


async def http_error_handler(request, exc):
    code = 'not_found' if exc.status_code == 404 else (
        'method_not_allowed' if exc.status_code == 405 else 'http_error'
    )
    message = 'Resource not found' if exc.status_code == 404 else (
        'HTTP method is not allowed' if exc.status_code == 405 else 'Request failed'
    )
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=exc.status_code)


def create_app(root=None, *, token_users=None):
    """Create the HTTP application. ``token_users`` is digest-to-user-id config for tests/embedding."""
    routes = [
        Route('/health', health, methods=['GET']),
        Route('/projects', list_projects, methods=['GET']),
        Route('/projects', create_project, methods=['POST']),
        Route('/projects/{project_id}', get_project, methods=['GET']),
        Route('/projects/{project_id}/plan', plan_project, methods=['POST']),
        Route('/projects/{project_id}/approval', plan_approval, methods=['GET', 'POST']),
        Route('/projects/{project_id}/tasks', list_tasks, methods=['GET']),
        Route('/projects/{project_id}/tasks', create_task, methods=['POST']),
        Route('/projects/{project_id}/tasks/{task_id}', get_task, methods=['GET']),
        Route('/projects/{project_id}/runs', list_runs, methods=['GET']),
        Route('/projects/{project_id}/run', execute_project, methods=['POST']),
        Route('/projects/{project_id}/resume', execute_project, methods=['POST']),
    ]
    app = Starlette(
        routes=routes,
        exception_handlers={
            ApiError: api_error_handler,
            StarletteHTTPException: http_error_handler,
            psycopg.Error: database_error_handler,
            Exception: unexpected_error_handler,
        },
    )
    app.state.root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    app.state.authenticator = ApiAuthenticator(token_users)
    app.add_middleware(BaseHTTPMiddleware, dispatch=add_authentication)
    return app
