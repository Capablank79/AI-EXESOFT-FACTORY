"""CLI para operar Factory mediante el Runtime PostgreSQL autorizado."""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg

from engine.agent_runtime import AgentRuntime
from engine.agents import configured_model_adapter, SandyDeveloperAdapter, SandyQAAdapter
from engine.postgres_runtime import PostgresRuntime
from engine.orchestrator import Orchestrator
from engine.runtime import Result
from engine.sandy_adapter import SandySandboxAdapter
from infrastructure.repositories import ProjectsRepository
from infrastructure.repositories.quality import QualityRepository
from infrastructure.uow import UnitOfWork
from quality.evidence import record_evidence
from infrastructure.observability import configure_logging

ROOT = Path(__file__).resolve().parent


def _safe_diagnostic(value, limit=1200):
    text = str(value or '')[-limit:]
    text = re.sub(r'(?i)(password|passwd|token|secret|api[_-]?key)(\s*[:=]\s*)[^\s,;]+',
                  r'\1\2[REDACTED]', text)
    return re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', text)


def _quality_decisions(runtime, project_id):
    validations = QualityRepository(tenant_id=runtime.tenant_id).list_validations(
        runtime._as_uuid(project_id), limit=1000
    )
    return [{
        'id': str(item.id), 'project_id': str(item.project_id),
        'task_id': str(item.task_id) if item.task_id else None,
        'approved': item.status == 'passed',
        'reason': (item.metrics or {}).get('reason', ''),
        'created_at': item.created_at,
        'status': item.status,
        'run_id': (item.metrics or {}).get('run_id'),
    } for item in validations]


def _current_plan_approval(runtime, project_id):
    from infrastructure.plan_approval import approval_status
    from infrastructure.uow import UnitOfWork
    with UnitOfWork(tenant_id=runtime.tenant_id) as uow:
        return approval_status(
            uow._get_connection(), runtime.tenant_id, runtime._as_uuid(project_id),
        )


def _internal_sandy_evidence(workspace):
    """Produce deterministic harness evidence for the explicitly simulated verify command."""
    output = 'Ran 1 test'
    return record_evidence(workspace, {
        'schema': 'exesoft.quality.test.v1', 'id': uuid4().hex,
        'kind': 'test', 'runner': 'sandy', 'status': 'passed', 'returncode': 0,
        'sandy_exit_code': 0, 'output': output,
        'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
        'tests_run': 1, 'result_sha256': '0' * 64, 'partial_ok': False,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })


def run_verification(root=ROOT, *, tenant_id=None):
    """Run the six-role simulated pipeline through PostgreSQL and its Quality Gate."""
    class MockAdapter:
        def __init__(self, agent):
            self.agent = agent

        def execute(self, task, project, context):
            evidence = (_internal_sandy_evidence(Path(context['workspace']))
                        if self.agent in {'developer', 'qa'} else ())
            if isinstance(evidence, str):
                evidence = (evidence,)
            return Result(True, f'{self.agent} verification passed', evidence,
                          approved=self.agent == 'qa')

    runtime = AgentRuntime(root, adapters={
        role: MockAdapter(role)
        for role in ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')
    }, tenant_id=tenant_id)
    project_id = runtime.orchestrator.create_project(
        f'CLI internal verification {uuid4()}', 'Verify PostgreSQL runtime command path'
    )
    runtime.orchestrator.plan_skeleton(project_id)
    history = runtime.run(project_id, max_steps=15)
    project = runtime.brain.get_project(project_id)
    print(json.dumps({
        'project_id': project_id, 'state': project.status,
        'agents_completed': [task['status'] for task in runtime.brain.tasks(project_id)],
        'quality_decisions': _quality_decisions(runtime, project_id),
    }, indent=2, ensure_ascii=False, default=str))
    return project.status == 'done' and bool(history)


def _default_execution_adapters(root, project_id, sandbox=None):
    workspace = Path(root) / 'workspaces' / str(project_id)
    context_error = SandySandboxAdapter.execution_context_error()
    if context_error:
        return None, context_error
    try:
        sandbox = sandbox or SandySandboxAdapter()
        if not sandbox._available:
            raise RuntimeError('Sandy no está disponible')
    except (FileNotFoundError, RuntimeError) as exc:
        return None, f'Sandy no disponible; ejecución fail-closed: {type(exc).__name__}'
    preflight = sandbox.preflight(workspace, timeout=15)
    if not preflight.success:
        detail = _safe_diagnostic(getattr(preflight, 'stderr', ''))
        return None, f'Sandy preflight falló ({preflight.sandy_exit_code}): {detail}'
    model = configured_model_adapter()
    return {
        'pm': model, 'architect': model, 'ux': model,
        'developer': SandyDeveloperAdapter(sandbox=sandbox, model=model),
        'qa': SandyQAAdapter(sandbox=sandbox), 'devops': model,
    }, None


def run_real_task(project_name: str, task_description: str, *, root=None,
                  max_steps=30, adapters=None, sandbox=None, tenant_id=None):
    """Create and run a project through PostgreSQL-backed AgentRuntime."""
    root = Path(root).resolve() if root is not None else ROOT
    if not 1 <= max_steps <= 100:
        raise ValueError('max_steps debe estar entre 1 y 100')
    data_runtime = PostgresRuntime(root, tenant_id=tenant_id)
    project_id = data_runtime.orchestrator.create_project(project_name, task_description)
    data_runtime.orchestrator.plan_skeleton(project_id)
    approval = _current_plan_approval(data_runtime, project_id)
    if not approval['approved']:
        return {
            'project_id': project_id, 'status': 'planned',
            'execution_status': 'approval_required', 'plan_hash': approval['plan_hash'],
            'history': [], 'tasks': data_runtime.brain.tasks(project_id),
            'quality_decisions': [],
        }
    if adapters is None:
        configured, preflight_error = _default_execution_adapters(root, project_id, sandbox=sandbox)
        if preflight_error:
            project = data_runtime._project(project_id)
            return {
                'project_id': project_id, 'status': project['status'],
                'execution_status': 'blocked', 'preflight': preflight_error,
                'workspace': str(root / 'workspaces' / project_id), 'history': [],
                'tasks': data_runtime.brain.tasks(project_id), 'quality_decisions': [],
            }
        adapters = configured
    runtime = AgentRuntime(root, adapters=adapters, tenant_id=data_runtime.tenant_id)
    history = runtime.run(project_id, max_steps=max_steps)
    project = runtime.brain.get_project(project_id)
    return {
        'project_id': project_id, 'status': project.status,
        'execution_status': 'completed' if project.status == 'done' else project.status,
        'workspace': str(root / 'workspaces' / project_id), 'history': history,
        'tasks': runtime.brain.tasks(project_id),
        'quality_decisions': _quality_decisions(runtime, project_id),
    }


def resume_real_task(project_id: str, *, root=None, max_steps=30,
                     adapters=None, tenant_id=None):
    """Resume one tenant-visible project using PostgreSQL Runtime recovery."""
    root = Path(root).resolve() if root is not None else ROOT
    if not 1 <= max_steps <= 100:
        raise ValueError('max_steps debe estar entre 1 y 100')
    data_runtime = PostgresRuntime(root, tenant_id=tenant_id)
    if data_runtime.brain.get_project(project_id) is None:
        raise KeyError('Project not found in this tenant')
    approval = _current_plan_approval(data_runtime, project_id)
    if not approval['approved']:
        return {
            'project_id': project_id, 'status': 'planned',
            'execution_status': 'approval_required', 'plan_hash': approval['plan_hash'],
            'history': [], 'tasks': data_runtime.brain.tasks(project_id),
            'quality_decisions': [],
        }
    if adapters is None:
        adapters, preflight_error = _default_execution_adapters(root, project_id)
        if preflight_error:
            project = data_runtime._project(project_id)
            return {
                'project_id': project_id, 'status': project['status'],
                'execution_status': 'blocked', 'preflight': preflight_error,
                'history': [], 'tasks': data_runtime.brain.tasks(project_id),
                'quality_decisions': _quality_decisions(data_runtime, project_id),
            }
    runtime = AgentRuntime(root, adapters=adapters, tenant_id=data_runtime.tenant_id)
    project = runtime._project(project_id)
    history = runtime.run(project_id, max_steps=max_steps)
    project = runtime.brain.get_project(project_id)
    return {
        'project_id': project_id, 'status': project.status,
        'execution_status': 'completed' if project.status == 'done' else project.status,
        'history': history, 'tasks': runtime.brain.tasks(project_id),
        'quality_decisions': _quality_decisions(runtime, project_id),
    }


def _parser():
    parser = argparse.ArgumentParser(description='Exesoft AI Factory — PostgreSQL Runtime')
    parser.add_argument('--root', dest='root_dir', help='Directorio raíz del workspace')
    parser.add_argument('--tenant-id', help='Tenant UUID (por defecto: EXESOFT_TENANT_ID)')
    sub = parser.add_subparsers(dest='command', required=True)

    create = sub.add_parser('create', help='Crear proyecto')
    create.add_argument('name')
    create.add_argument('request')
    plan = sub.add_parser('plan', help='Planificar proyecto')
    plan.add_argument('project_id')
    plan.add_argument('--legacy-skeleton', action='store_true',
                      help='Use the fixed legacy ODD plan instead of requirements planning')
    plan.add_argument('--replan-authorized-hash',
                      help='Current plan fingerprint explicitly authorized for replacement')
    approval = sub.add_parser('approval', help='Ver aprobación vigente del plan')
    approval.add_argument('project_id')
    approve = sub.add_parser('approve', help='Aprobar explícitamente el hash vigente del plan')
    approve.add_argument('project_id')
    approve.add_argument('plan_hash')
    revoke = sub.add_parser('revoke-approval', help='Revocar aprobación del hash vigente')
    revoke.add_argument('project_id')
    revoke.add_argument('plan_hash')
    status = sub.add_parser('status', help='Ver estado de proyecto y tareas')
    status.add_argument('project_id')
    tasks = sub.add_parser('tasks', help='Listar tareas del proyecto')
    tasks.add_argument('project_id')
    task = sub.add_parser('task', help='Ver tarea por ID dentro de un proyecto')
    task.add_argument('project_id')
    task.add_argument('task_id')
    projects = sub.add_parser('projects', help='Listar proyectos del tenant')
    projects.add_argument('--limit', type=int, default=100)
    runs = sub.add_parser('runs', help='Listar ejecuciones del proyecto')
    runs.add_argument('project_id')
    remember = sub.add_parser('remember', help='Guardar conocimiento')
    remember.add_argument('title')
    remember.add_argument('body')
    remember.add_argument('--source', required=True)
    recall = sub.add_parser('recall', help='Buscar conocimiento')
    recall.add_argument('query')
    verify = sub.add_parser('verify', help='Verificar la ruta interna de ejecución')
    verify.add_argument('--internal', action='store_true')
    run = sub.add_parser('run', help='Crear proyecto y ejecutar su plan')
    run.add_argument('name')
    run.add_argument('request')
    run.add_argument('--max-steps', type=int, default=30)
    resume = sub.add_parser('resume', help='Recuperar y continuar un proyecto')
    resume.add_argument('project_id')
    resume.add_argument('--max-steps', type=int, default=30)
    return parser


def main(argv=None):
    configure_logging()
    args = _parser().parse_args(argv)
    root = Path(args.root_dir).resolve() if args.root_dir else ROOT
    tenant_id = args.tenant_id or os.environ.get('EXESOFT_TENANT_ID')
    try:
        if args.command == 'verify':
            if not args.internal:
                print('Error: indique --internal para ejecutar la verificación simulada', file=sys.stderr)
                return 2
            print('Verificación:', 'EXITOSA' if run_verification(root, tenant_id=tenant_id) else 'FALLIDA')
            return 0
        if args.command == 'run':
            out = run_real_task(args.name, args.request, root=root,
                                max_steps=args.max_steps, tenant_id=tenant_id)
        elif args.command == 'resume':
            out = resume_real_task(args.project_id, root=root,
                                   max_steps=args.max_steps, tenant_id=tenant_id)
        else:
            runtime = PostgresRuntime(root, tenant_id=tenant_id)
            app = runtime.orchestrator
            if args.command == 'create':
                out = {'project_id': app.create_project(args.name, args.request)}
            elif args.command == 'plan':
                if runtime.brain.get_project(args.project_id) is None:
                    raise KeyError('Project not found in this tenant')
                if args.legacy_skeleton:
                    out = {'tasks': app.plan_skeleton(args.project_id), 'planner': 'legacy_skeleton'}
                else:
                    if args.replan_authorized_hash:
                        planned = app.plan_from_requirements(
                            args.project_id,
                            replan_authorized_hash=args.replan_authorized_hash,
                        )
                    else:
                        planned = app.plan_from_requirements(args.project_id)
                    out = {**planned, 'planner': 'requirements_gateway'}
            elif args.command in {'approval', 'approve', 'revoke-approval'}:
                from infrastructure.plan_approval import approval_status, record_plan_decision
                project_id = runtime._as_uuid(args.project_id)
                if runtime.brain.get_project(str(project_id)) is None:
                    raise KeyError('Project not found in this tenant')
                if args.command == 'approval':
                    with UnitOfWork(tenant_id=runtime.tenant_id) as uow:
                        out = approval_status(
                            uow._get_connection(), runtime.tenant_id, project_id,
                        )
                else:
                    if len(args.plan_hash) != 64 or any(
                            char not in '0123456789abcdef' for char in args.plan_hash):
                        raise ValueError('plan_hash must be a lowercase SHA-256 fingerprint')
                    with UnitOfWork(tenant_id=runtime.tenant_id) as uow:
                        out = record_plan_decision(
                            uow, project_id, actor_id='local-operator',
                            expected_hash=args.plan_hash,
                            approve=args.command == 'approve',
                        )
            elif args.command == 'status':
                if runtime.brain.get_project(args.project_id) is None:
                    raise KeyError('Project not found in this tenant')
                project = runtime._project(args.project_id)
                out = {'project': project, 'tasks': runtime.brain.tasks(args.project_id),
                       'ready': app.ready_tasks(args.project_id)}
            elif args.command == 'tasks':
                if runtime.brain.get_project(args.project_id) is None:
                    raise KeyError('Project not found in this tenant')
                out = {'tasks': runtime.brain.tasks(args.project_id)}
            elif args.command == 'task':
                if runtime.brain.get_project(args.project_id) is None:
                    raise KeyError('Project not found in this tenant')
                found = next((item for item in runtime.brain.tasks(args.project_id)
                              if item['id'] == str(runtime._as_uuid(args.task_id))), None)
                if found is None:
                    raise KeyError('Task not found in this project')
                out = {'task': found}
            elif args.command == 'projects':
                if args.limit < 1 or args.limit > 1000:
                    raise ValueError('limit debe estar entre 1 y 1000')
                out = {'projects': [vars(item) for item in ProjectsRepository(
                    tenant_id=runtime.tenant_id).list_projects(runtime.tenant_id, limit=args.limit)]}
            elif args.command == 'runs':
                if runtime.brain.get_project(args.project_id) is None:
                    raise KeyError('Project not found in this tenant')
                out = {'runs': runtime.runs(args.project_id)}
            elif args.command == 'remember':
                out = {'knowledge_id': runtime.brain.remember(
                    'note', args.title, args.body, args.source)}
            else:
                out = runtime.brain.recall(args.query)
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 2 if out.get('execution_status') == 'approval_required' else 0
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else 'Requested record not found'
        print(f'Error: {message}', file=sys.stderr)
        return 2
    except (ValueError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    except psycopg.Error:
        # Driver exceptions can include connection details; never echo them.
        print('Error: PostgreSQL operation failed; connection details omitted', file=sys.stderr)
        return 3
    except Exception as exc:
        print(f'Error: operation failed ({type(exc).__name__})', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
