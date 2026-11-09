"""PostgreSQL-backed runtime using repository and UnitOfWork persistence."""
import json
import logging
import os
from pathlib import Path
from uuid import UUID, uuid4
from datetime import datetime, timezone

from engine.odd import ODDEngine
from engine.handoffs import AgentHandoff
from engine.orchestrator import Orchestrator
from infrastructure.repositories import ExecutionRepository, IdentityRepository, MemoryRepository
from infrastructure.services.consistency import ConcurrencyError
from infrastructure.uow import UnitOfWork
from quality.evidence import validate_sandbox_execution_evidence
from infrastructure.observability import log_event

_logger = logging.getLogger('factory.runtime')


class PostgresRuntime:
    def __init__(self, root, adapters=None, tenant_id=None):
        self.root = Path(root)
        self.tenant_id = self._resolve_tenant_id(tenant_id)
        self.orchestrator = Orchestrator(
            self.root, tenant_id=self.tenant_id, backend='postgres'
        )
        self.brain = self.orchestrator.brain
        self.adapters = adapters or {}
        self.odd = ODDEngine(self.brain)

    @staticmethod
    def _resolve_tenant_id(tenant_id):
        configured = tenant_id or os.environ.get('EXESOFT_TENANT_ID')
        if configured:
            return configured if isinstance(configured, UUID) else UUID(str(configured))
        active_tenants = IdentityRepository().list_tenants(active_only=True)
        if len(active_tenants) != 1:
            raise ValueError(
                'Pass tenant_id or set EXESOFT_TENANT_ID when multiple tenants are active'
            )
        return active_tenants[0].id

    @staticmethod
    def _as_uuid(value):
        return value if isinstance(value, UUID) else UUID(str(value))

    def recover(self, project_id=None):
        """Recover orphaned runs without replaying external side effects."""
        recovered = []
        pid = self._as_uuid(project_id) if project_id is not None else None
        with UnitOfWork(tenant_id=self.tenant_id) as uow:
            tasks = uow.planning.list_running_tasks(pid)
            for task in tasks:
                target = 'pending' if task.attempts < task.max_attempts else 'failed'
                try:
                    uow.planning.update_task_status(task.id, target, expected_version=task.version)
                except ConcurrencyError:
                    continue
                runs = uow.execution._fetch_all(
                    "SELECT id FROM task_runs WHERE project_id = %s AND task_id = %s AND status = 'running'",
                    [str(task.project_id), str(task.id)]
                )
                interrupted_runs = []
                for run in runs:
                    uow.execution.complete_task_run(
                        run['id'], 'interrupted', summary='Interrupted by runtime recovery'
                    )
                    validation = uow.quality.create_validation(
                        quality_gate_id=None, project_id=task.project_id,
                        task_id=task.id, tenant_id=self.tenant_id
                    )
                    uow.quality.update_validation_status(
                        validation.id, 'failed', evidence=[],
                        metrics={
                            'gate': 'f6_execution', 'run_id': str(run['id']),
                            'agent': task.agent, 'approved': False,
                            'reason': 'Execution was interrupted before Quality Gate approval',
                            'checks': {'execution_integrity': False, 'recovered': True},
                        }, finished_at=datetime.now(timezone.utc)
                    )
                    uow.events.emit_event(
                        'quality.rejected', {
                            'validation_id': str(validation.id),
                            'run_id': str(run['id']), 'approved': False,
                            'reason': 'Execution was interrupted before Quality Gate approval',
                            'evidence': [],
                        }, tenant_id=self.tenant_id,
                        project_id=task.project_id, task_id=task.id
                    )
                    interrupted_runs.append(str(run['id']))
                recovery_runs = interrupted_runs or [None]
                for interrupted_run_id in recovery_runs:
                    uow.events.emit_event(
                        'task.recovered', {
                            'status': target, 'run_id': interrupted_run_id,
                            'interrupted_run_ids': interrupted_runs,
                        }, tenant_id=self.tenant_id,
                        project_id=task.project_id, task_id=task.id
                    )
                recovered.append((str(task.project_id), str(task.id), target))
            if recovered:
                uow.commit()
        for recovered_project, task_id, target in recovered:
            log_event(_logger, logging.WARNING, 'task.recovered', component='runtime',
                      tenant_id=str(self.tenant_id), project_id=recovered_project,
                      task_id=task_id, outcome=target)
            self.odd.sync_brain_task(recovered_project, task_id, target)
        return len(recovered)

    def _project(self, project_id):
        project = self.brain.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        value = dict(vars(project))
        value['id'] = str(value['id'])
        value['tenant_id'] = str(value['tenant_id'])
        request = value.get('request')
        if isinstance(request, dict) and set(request) == {'description'}:
            request = request['description']
        value['request'] = request
        return value

    def _dispatch(self, task, project):
        from engine.runtime import Result

        adapter = self.adapters.get(task['agent'])
        if adapter is None:
            return Result(False, 'Sin adaptador registrado para ' + task['agent'])
        context = {
            'workspace': str(self.root / 'workspaces' / project['id']),
            'request': project['request'], 'task_id': task['id'],
            'tenant_id': project.get('tenant_id', str(self.tenant_id)),
            'project_id': project['id'], 'run_id': task.get('_run_id'),
            'acceptance_criteria': self.odd.acceptance_for_brain_task(
                project['id'], task['id']),
            # Stable across attempts so adapters can deduplicate external effects.
            'idempotency_key': task.get('idempotency_key') or task['id'],
        }
        return adapter.execute(task, project, context)

    def step(self, project_id):
        from engine.runtime import Result
        from infrastructure.plan_approval import approval_status

        # Fail closed before recovery, project-state transitions, or dispatch.
        with UnitOfWork(tenant_id=self.tenant_id) as approval_uow:
            state = approval_status(
                approval_uow._get_connection(), self.tenant_id,
                self._as_uuid(project_id), lock=True,
            )
        if not state['approved']:
            from infrastructure.plan_approval import PlanApprovalError
            raise PlanApprovalError(
                'Explicit approval for the current plan is required before task execution '
                f'(plan_hash={state["plan_hash"]})'
            )

        project = self._project(project_id)
        if project['status'] in ('done', 'cancelled', 'blocked'):
            return {'state': project['status'], 'task': None}
        ready = self.orchestrator.ready_tasks(project_id)
        if not ready:
            tasks = self.brain.tasks(project_id)
            if tasks and all(task['status'] == 'done' for task in tasks):
                self.brain.set_project_status(project_id, 'done')
                return {'state': 'done', 'task': None}
            if any(task['status'] == 'running' for task in tasks):
                return {'state': 'running', 'task': None}
            self.brain.set_project_status(project_id, 'blocked')
            return {'state': 'blocked', 'task': None}

        task = ready[0]
        self.odd.ensure_task_links(project_id)
        task_id = self._as_uuid(task['id'])
        project_uuid = self._as_uuid(project_id)
        run_id = uuid4()
        try:
            with UnitOfWork(tenant_id=self.tenant_id) as uow:
                current = uow.planning.get_task(task_id)
                if current is None or current.status != 'pending':
                    return {'state': 'concurrent_update', 'task': task['id']}
                claimed = uow.planning.claim_task_for_execution(
                    task_id, project_uuid, expected_version=current.version
                )
                if claimed is None:
                    return {'state': 'concurrent_update', 'task': task['id']}
                uow.events.emit_event(
                    'task.claimed', {
                        'run_id': str(run_id), 'attempt': claimed.attempts,
                        'version': claimed.version,
                    }, tenant_id=self.tenant_id,
                    project_id=project_uuid, task_id=task_id
                )
                uow.execution.create_task_run(
                    tenant_id=self.tenant_id, task_id=task_id,
                    project_id=project_uuid, agent_name=task['agent'], run_id=run_id
                )
                uow.events.emit_event(
                    'task.started', {
                        'run_id': str(run_id), 'attempt': claimed.attempts,
                        'version': claimed.version,
                    }, tenant_id=self.tenant_id,
                    project_id=project_uuid, task_id=task_id
                )
                if project['status'] != 'running':
                    uow.projects.update_project_status(project_uuid, 'running')
                    uow.events.emit_event(
                        'project.status', {'status': 'running'}, tenant_id=self.tenant_id,
                        project_id=project_uuid
                    )
                uow.commit()
        except ConcurrencyError:
            log_event(_logger, logging.WARNING, 'task.claim.conflict', component='runtime',
                      tenant_id=str(self.tenant_id), project_id=str(project_uuid),
                      task_id=str(task_id), outcome='concurrent_update')
            return {'state': 'concurrent_update', 'task': task['id']}

        log_event(_logger, logging.INFO, 'task.started', component='runtime',
                  tenant_id=str(self.tenant_id), project_id=str(project_uuid),
                  task_id=str(task_id), run_id=str(run_id), agent=task['agent'],
                  attempt=claimed.attempts, outcome='running')
        self.odd.sync_brain_task(project_id, task['id'], 'running')
        try:
            result = self._dispatch({**task, '_run_id': str(run_id)}, project)
            if not isinstance(result, Result):
                raise TypeError('El adaptador debe devolver Result')
        except Exception as exc:
            log_event(_logger, logging.ERROR, 'agent.execution.failed', component='runtime',
                      tenant_id=str(self.tenant_id), project_id=str(project_uuid),
                      task_id=str(task_id), run_id=str(run_id), agent=task['agent'],
                      error_type=type(exc).__name__, outcome='failed')
            result = Result(False, f'Error del adaptador: {type(exc).__name__}')
        if task['agent'] == 'qa' and result.success and (not result.approved or not result.evidence):
            result = Result(False, 'QA requiere aprobación explícita y evidencias verificables', result.evidence)
        if not result.success:
            log_event(_logger, logging.WARNING, 'agent.execution.rejected_or_failed',
                      component='runtime', tenant_id=str(self.tenant_id),
                      project_id=str(project_uuid), task_id=str(task_id), run_id=str(run_id),
                      agent=task['agent'], outcome='failed')
            if task['agent'] in {'developer', 'qa'}:
                log_event(_logger, logging.ERROR, 'sandbox.execution.failed',
                          component='runtime', tenant_id=str(self.tenant_id),
                          project_id=str(project_uuid), task_id=str(task_id),
                          run_id=str(run_id), agent=task['agent'], outcome='failed')

        workspace = self.root / 'workspaces' / str(project_uuid)
        if not result.success:
            quality_decision = {
                'approved': False, 'reason': 'El resultado del agente fue fallido',
                'checks': {'agent_success': False}, 'evidence': list(result.evidence),
            }
        elif task['agent'] in {'developer', 'qa'}:
            quality_decision = validate_sandbox_execution_evidence(workspace, result.evidence)
            quality_decision['checks']['agent_success'] = True
            if task['agent'] == 'qa' and not result.approved:
                quality_decision['approved'] = False
                quality_decision['reason'] = 'QA requiere aprobación explícita'
                quality_decision['checks']['qa_approval'] = False
            else:
                quality_decision['checks']['qa_approval'] = task['agent'] != 'qa' or result.approved
        else:
            quality_decision = {
                'approved': True, 'reason': 'Ejecución del agente completada',
                'checks': {'agent_success': True}, 'evidence': list(result.evidence),
            }
        if result.success and not quality_decision['approved']:
            result = Result(
                False, f'{result.summary}; Quality Gate rechazó la ejecución: '
                f"{quality_decision['reason']}", result.evidence, approved=False
            )

        try:
            with UnitOfWork(tenant_id=self.tenant_id) as uow:
                current = uow.planning.get_task(task_id)
                if current is None or current.status != 'running':
                    return {'state': 'concurrent_update', 'task': task['id']}
                artifact_records = []
                for reference in result.evidence:
                    # Existing adapters also use opaque references (for example
                    # artifact:foo); register only filesystem evidence paths.
                    value = str(reference)
                    if value.startswith(('artifact:', 'evidence:')):
                        continue
                    if '/' not in value and '\\' not in value:
                        continue
                    try:
                        artifact = uow.artifacts.register_workspace_artifact(
                            workspace, value, tenant_id=self.tenant_id,
                            project_id=project_uuid, task_run_id=run_id,
                            artifact_type='test'
                        )
                    except (ValueError, PermissionError, OSError):
                        # Invalid paths are rejected by the gate and remain only
                        # as the original execution reference in run history.
                        continue
                    artifact_records.append(artifact)
                if quality_decision['approved'] and 'artifact_hashes' in quality_decision:
                    registered_by_path = {item.path: item for item in artifact_records}
                    expected_hashes = quality_decision.get('artifact_hashes', {})
                    for reference in quality_decision.get('evidence', ()):
                        normalized = str(reference).replace('\\', '/')
                        artifact = registered_by_path.get(normalized)
                        if (artifact is None or artifact.task_run_id != run_id
                                or artifact.project_id != project_uuid
                                or artifact.tenant_id != self.tenant_id
                                or artifact.sha256_hash != expected_hashes.get(reference)):
                            quality_decision['approved'] = False
                            quality_decision['reason'] = (
                                'La evidencia no quedó persistida como artefacto íntegro '
                                'y correlacionado con esta ejecución'
                            )
                            quality_decision['checks']['artifact_persisted'] = False
                            break
                    else:
                        quality_decision['checks']['artifact_persisted'] = True
                if result.success and not quality_decision['approved']:
                    result = Result(
                        False, f'{result.summary}; Quality Gate rechazó la ejecución: '
                        f"{quality_decision['reason']}", result.evidence, approved=False
                    )
                task_status = 'done' if result.success else (
                    'pending' if current.attempts < current.max_attempts else 'failed'
                )
                validation = uow.quality.create_validation(
                    quality_gate_id=None, project_id=project_uuid,
                    task_id=task_id, tenant_id=self.tenant_id
                )
                uow.quality.update_validation_status(
                    validation.id, 'passed' if quality_decision['approved'] else 'failed',
                    evidence=list(result.evidence),
                    metrics={
                        'gate': 'f6_execution', 'run_id': str(run_id),
                        'agent': task['agent'], 'approved': quality_decision['approved'],
                        'reason': quality_decision['reason'],
                        'checks': quality_decision['checks'],
                    }, started_at=datetime.now(timezone.utc),
                    finished_at=datetime.now(timezone.utc)
                )
                uow.planning.update_task_status(
                    task_id, task_status, expected_version=current.version
                )
                uow.execution.complete_task_run(
                    run_id, 'succeeded' if result.success else 'failed',
                    summary=result.summary, evidence=list(result.evidence)
                )
                uow.events.emit_event(
                    'quality.' + ('approved' if quality_decision['approved'] else 'rejected'),
                    {
                        'validation_id': str(validation.id), 'run_id': str(run_id),
                        'approved': quality_decision['approved'],
                        'reason': quality_decision['reason'],
                        'evidence': list(result.evidence),
                    }, tenant_id=self.tenant_id,
                    project_id=project_uuid, task_id=task_id
                )
                uow.events.emit_event(
                    'task.' + ('succeeded' if result.success else 'failed'),
                    {
                        'summary': result.summary, 'status': task_status,
                        'run_id': str(run_id), 'attempt': current.attempts,
                    },
                    tenant_id=self.tenant_id, project_id=project_uuid, task_id=task_id
                )
                if task_status == 'pending':
                    uow.events.emit_event(
                        'task.retry', {
                            'run_id': str(run_id), 'attempt': current.attempts,
                            'next_attempt': current.attempts + 1,
                            'max_attempts': current.max_attempts,
                            'summary': result.summary,
                        }, tenant_id=self.tenant_id,
                        project_id=project_uuid, task_id=task_id
                    )
                handoff_id = None
                if task_status == 'done':
                    project_tasks = uow.planning.list_tasks_by_project(project_uuid, limit=1000)
                    next_agents = tuple(dict.fromkeys(
                        candidate.agent for candidate in project_tasks
                        if str(task_id) in {str(dependency) for dependency in (candidate.depends_on or [])}
                        and candidate.status == 'pending'
                    ))
                    previous_handoffs = uow.memory.list_agent_handoffs(project_uuid, limit=1000)
                    handoff = AgentHandoff(
                        handoff_id=run_id, tenant_id=self.tenant_id,
                        project_id=project_uuid, task_id=task_id, run_id=run_id,
                        from_agent=task['agent'], to_agents=next_agents,
                        task_status=task_status, attempts=current.attempts,
                        summary=result.summary,
                        approved=bool(result.approved),
                        decisions=({'agent': task['agent'], 'success': True,
                                    'approved': bool(result.approved)},),
                        artifacts=tuple(str(item) for item in result.evidence),
                        task_context={
                            'title': task['title'], 'status': task_status,
                            'attempts': current.attempts,
                            'max_attempts': current.max_attempts,
                            'artifact_ids': [str(item.id) for item in artifact_records],
                        },
                        project_context={
                            'name': project['name'], 'request': project['request'],
                        },
                        previous_handoff_ids=tuple(
                            str(item.id) for item in previous_handoffs[-20:]
                            if str(item.id) != str(run_id)
                        ),
                    )
                    uow.memory.create_agent_handoff(handoff)
                    handoff_id = handoff.handoff_id
                    uow.events.emit_event(
                        'agent.handoff', {
                            'handoff_id': str(handoff_id), 'run_id': str(run_id),
                            'from_agent': task['agent'], 'to_agents': list(next_agents),
                        }, tenant_id=self.tenant_id,
                        project_id=project_uuid, task_id=task_id
                    )
                if task_status == 'failed':
                    uow.projects.update_project_status(project_uuid, 'blocked')
                    uow.events.emit_event(
                        'project.status', {'status': 'blocked'}, tenant_id=self.tenant_id,
                        project_id=project_uuid
                    )
                if artifact_records:
                    uow.events.emit_event(
                        'artifact.registered', {
                            'run_id': str(run_id),
                            'artifact_ids': [str(item.id) for item in artifact_records],
                            'paths': [item.path for item in artifact_records],
                        }, tenant_id=self.tenant_id,
                        project_id=project_uuid, task_id=task_id
                    )
                uow.commit()
        except ConcurrencyError:
            return {'state': 'concurrent_update', 'task': task['id']}

        log_event(_logger, logging.INFO if quality_decision['approved'] else logging.WARNING,
                  'quality.decision', component='runtime', tenant_id=str(self.tenant_id),
                  project_id=str(project_uuid), task_id=str(task_id), run_id=str(run_id),
                  agent=task['agent'], outcome='approved' if quality_decision['approved'] else 'rejected')
        log_event(_logger, logging.INFO if task_status == 'done' else logging.WARNING,
                  'task.execution.finished', component='runtime', tenant_id=str(self.tenant_id),
                  project_id=str(project_uuid), task_id=str(task_id), run_id=str(run_id),
                  agent=task['agent'], outcome=task_status, attempt=task['attempts'])
        if task_status == 'pending':
            log_event(_logger, logging.WARNING, 'task.retry.scheduled', component='runtime',
                      tenant_id=str(self.tenant_id), project_id=str(project_uuid),
                      task_id=str(task_id), run_id=str(run_id), agent=task['agent'],
                      attempt=task['attempts'] + 1, outcome='retry')
        self.odd.sync_brain_task(
            project_id, task['id'], task_status,
            evidence=list(result.evidence) if task_status == 'done' else None
        )
        return {
            'state': task_status, 'task': task['id'], 'agent': task['agent'],
            'run': str(run_id), 'summary': result.summary,
            'handoff': str(handoff_id) if task_status == 'done' else None,
        }

    def run(self, project_id, max_steps=20):
        if max_steps < 1:
            raise ValueError('max_steps debe ser positivo')
        from infrastructure.plan_approval import approval_status, PlanApprovalError
        with UnitOfWork(tenant_id=self.tenant_id) as approval_uow:
            state = approval_status(
                approval_uow._get_connection(), self.tenant_id,
                self._as_uuid(project_id), lock=True,
            )
        if not state['approved']:
            raise PlanApprovalError(
                'Explicit approval for the current plan is required before task execution '
                f'(plan_hash={state["plan_hash"] or "unplanned"})'
            )
        self.recover(project_id)
        history = []
        for _ in range(max_steps):
            outcome = self.step(project_id)
            history.append(outcome)
            if outcome['state'] in ('blocked', 'cancelled', 'concurrent_update') or outcome['task'] is None:
                break
        return history

    def runs(self, project_id):
        records = ExecutionRepository(tenant_id=self.tenant_id).list_task_runs(
            self._as_uuid(project_id)
        )
        records.sort(key=lambda run: (run.started_at, str(run.id)))
        return [{
            'id': str(run.id), 'task_id': str(run.task_id),
            'project_id': str(run.project_id), 'agent': run.agent_name,
            'status': run.status, 'started_at': run.started_at.isoformat(),
            'finished_at': run.finished_at.isoformat() if run.finished_at else None,
            'summary': run.summary, 'evidence': json.dumps(run.evidence or [])
        } for run in records]

    def handoffs(self, project_id, limit=100):
        """List this tenant's persisted project handoffs in chronological order."""
        records = MemoryRepository(tenant_id=self.tenant_id).list_agent_handoffs(
            self._as_uuid(project_id), limit=limit
        )
        return [json.loads(record.body) for record in records]

    def retrieve_artifact(self, project_id, artifact_id):
        """Retrieve bytes after tenant/project and on-disk integrity checks."""
        from infrastructure.repositories import ArtifactsRepository
        project_uuid = self._as_uuid(project_id)
        workspace = self.root / 'workspaces' / str(project_uuid)
        return ArtifactsRepository(tenant_id=self.tenant_id).retrieve_workspace_artifact(
            artifact_id, project_id=project_uuid, workspace=workspace
        )
