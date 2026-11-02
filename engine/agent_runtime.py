"""Incremental integration: brain retrieval + adapter registry without changing v0.2."""
from pathlib import Path
import re
from engine.runtime import Runtime
from engine.agents import FreeLLMAPIAdapter, SandyDeveloperAdapter, SandyQAAdapter
from engine.sandy_adapter import SandySandboxAdapter
from engine.runtime import Result


class UnavailableAdapter:
    """Explicit fail-closed result when the required isolated backend is absent."""
    def __init__(self, reason):
        self.reason = reason

    def execute(self, task, project, context):
        return Result(False, self.reason)

class AgentRuntime(Runtime):
    def __init__(self, root, adapters=None):
        if adapters is None:
            model = FreeLLMAPIAdapter()
            try:
                sandbox = SandySandboxAdapter()
                if not sandbox._available:
                    raise RuntimeError('Sandy CLI no supera su comprobación de disponibilidad')
                developer = SandyDeveloperAdapter(sandbox=sandbox, model=model)
                qa = SandyQAAdapter(sandbox=sandbox)
            except (FileNotFoundError, RuntimeError) as exc:
                developer = qa = UnavailableAdapter(f'Sandy no disponible; ejecución bloqueada: {exc}')
            adapters = {'pm': model, 'architect': model, 'ux': model,
                        'developer': developer, 'qa': qa, 'devops': model}
        super().__init__(root,adapters)

    def _dispatch(self, task, project):
        adapter=self.adapters.get(task['agent'])
        if adapter is None:
            return super()._dispatch(task,project)
        workspace=(self.root/'workspaces'/project['id']).resolve()
        workspace.mkdir(parents=True,exist_ok=True)
        # FTS5 escapes user text into token-safe quoted terms; malformed syntax cannot crash runtime.
        terms=re.findall(r'\w+', task['title'],flags=re.UNICODE)[:5]
        knowledge=[]
        if terms:
            try: knowledge=self.brain.recall(' OR '.join('"'+w.replace('"','')+'"' for w in terms),project['id'],limit=5)
            except Exception: knowledge=[]
        return adapter.execute(task,project,{'workspace':str(workspace), 'request':project['request'],
                                             'task_id':task['id'], 'knowledge':knowledge})
