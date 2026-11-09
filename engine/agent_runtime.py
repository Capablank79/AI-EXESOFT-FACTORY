"""Incremental integration: brain retrieval + adapter registry without changing v0.2."""
from pathlib import Path
import re
from engine.runtime import LegacySQLiteRuntime, Runtime
from engine.agents import configured_model_adapter, SandyDeveloperAdapter, SandyQAAdapter
from engine.sandy_adapter import SandySandboxAdapter
from engine.runtime import Result


class UnavailableAdapter:
    """Explicit fail-closed result when the required isolated backend is absent."""
    def __init__(self, reason):
        self.reason = reason

    def execute(self, task, project, context):
        return Result(False, self.reason)

def _build_adapters(adapters):
    if adapters is not None:
        return adapters
    model = configured_model_adapter()
    try:
        sandbox = SandySandboxAdapter()
        if not sandbox._available:
            raise RuntimeError('Sandy CLI no supera su comprobación de disponibilidad')
        developer = SandyDeveloperAdapter(sandbox=sandbox, model=model)
        qa = SandyQAAdapter(sandbox=sandbox)
    except (FileNotFoundError, RuntimeError) as exc:
        developer = qa = UnavailableAdapter(f'Sandy no disponible; ejecución bloqueada: {exc}')
    return {'pm': model, 'architect': model, 'ux': model,
            'developer': developer, 'qa': qa, 'devops': model}


class _AgentRuntimeDispatch:
    def _dispatch(self, task, project):
        adapter=self.adapters.get(task['agent'])
        if adapter is None:
            return super()._dispatch(task,project)
        workspace=(self.root/'workspaces'/project['id']).resolve()
        workspace.mkdir(parents=True,exist_ok=True)
        terms=re.findall(r'\w+', task['title'],flags=re.UNICODE)[:5]
        knowledge=[]
        handoffs = self.brain.agent_handoffs(project['id'], limit=10) \
            if hasattr(self.brain, 'agent_handoffs') else []
        if terms:
            found={}
            for term in terms:
                try:
                    for item in self.brain.recall(term,project['id'],limit=5):
                        found.setdefault(str(item.get('id')), item)
                except Exception:
                    continue
            knowledge=list(found.values())[:5]
        context = {
            'workspace': str(workspace), 'request': project['request'],
            'task_id': task['id'], 'project_id': project['id'],
            'tenant_id': project.get('tenant_id'), 'run_id': task.get('_run_id'),
            'acceptance_criteria': self.odd.acceptance_for_brain_task(
                project['id'], task['id']),
            'knowledge': knowledge, 'handoffs': handoffs,
        }
        adapter_task = {key: value for key, value in task.items() if key != '_run_id'}
        return adapter.execute(adapter_task, project, context)


class AgentRuntime(_AgentRuntimeDispatch, Runtime):
    def __init__(self, root, adapters=None, tenant_id=None):
        super().__init__(root, _build_adapters(adapters), tenant_id=tenant_id)


class LegacyAgentRuntime(_AgentRuntimeDispatch, LegacySQLiteRuntime):
    """SQLite-backed compatibility runtime retained for AutonomousEngine v0.5."""
    def __init__(self, root, adapters=None):
        super().__init__(root, _build_adapters(adapters))
