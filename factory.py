"""CLI local para el núcleo Exesoft AI Factory."""
import argparse
import json
from pathlib import Path
from engine.orchestrator import Orchestrator
from engine.autonomous import AutonomousEngine
from engine.runtime import Result
from engine.agents import FreeLLMAPIAdapter, SandyDeveloperAdapter, SandyQAAdapter
from engine.sandy_adapter import SandySandboxAdapter
from quality.evidence import run_tests, record_evidence, verify_test_evidence

ROOT = Path(__file__).resolve().parent

def run_verification(root=ROOT):
    """Ejecuta verificación del circuito interno de 6 agentes."""
    # Setup mock adapters for internal verification
    class MockAdapter:
        def __init__(self, agent_name):
            self.agent_name = agent_name

        def execute(self, task, project, context):
            responses = {
                'pm': 'Scope defined: circuit verification',
                'architect': 'Architecture verified',
                'ux': 'UX flow validated',
                'developer': 'Implementation verified',
                'qa': 'Tests pass',
                'devops': 'Delivery ready'
            }
            if self.agent_name in responses:
                return Result(True, responses[self.agent_name], (), approved=(self.agent_name=='qa'))
            return Result(False, 'Unknown agent')

    # La verificación interna usa adaptadores simulados y no inicializa backends externos.
    engine = AutonomousEngine(root, adapters={})
    adapters = {role: MockAdapter(role) for role in ['pm', 'architect', 'ux', 'developer', 'qa', 'devops']}
    engine.adapters = adapters

    # Create project
    project_id = engine.orchestrator.create_project('Verificacion Circuito', 'Verificar circuito de 6 agentes')
    engine.orchestrator.plan_skeleton(project_id)

    # Create workspace and test
    ws = root / 'workspaces' / project_id
    ws.mkdir(parents=True, exist_ok=True)

    tests_dir = ws / 'tests'
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / 'test_factory.py').write_text(
        'import unittest\nclass FactoryTest(unittest.TestCase):\n    def test_passes(self): self.assertTrue(True)\n',
        encoding='utf-8'
    )

    # Run tests and register evidence
    artifact = run_tests(ws)
    rel_path = record_evidence(ws, artifact)

    if verify_test_evidence(ws, rel_path):
        engine.register_test_evidence(project_id, rel_path)

    # Run pipeline
    history = engine.run_controlled(project_id, max_steps=15)
    project = engine.brain.get_project(project_id)

    print(json.dumps({
        'project_id': project_id,
        'state': project['status'],
        'agents_completed': [t['status'] for t in engine.brain.tasks(project_id)]
    }, indent=2, ensure_ascii=False))

    return project['status'] == 'done'


def run_real_task(project_name: str, task_description: str, *, root=None, max_steps=30, adapters=None, sandbox=None):
    """
    Ejecuta una tarea real con capacidad de escritura.

    Args:
        project_name: Nombre del proyecto
        task_description: Descripción de la tarea
        root: Raíz de datos (por defecto, la raíz de este proyecto)

    Returns:
        dict con resultados del pipeline
    """
    root = Path(root).resolve() if root is not None else ROOT
    engine = AutonomousEngine(root, adapters={})
    project_id = engine.orchestrator.create_project(project_name, task_description)
    engine.orchestrator.plan_skeleton(project_id)
    ws = root / 'workspaces' / project_id
    if adapters is None:
        context_error = SandySandboxAdapter.execution_context_error()
        if context_error:
            return {'project_id':project_id,'status':'planned','execution_status':'blocked',
                    'preflight':context_error,'workspace':str(ws),'history':[],
                    'tasks':engine.brain.tasks(project_id),'quality_decisions':[]}
        try:
            sandbox = sandbox or SandySandboxAdapter()
            if not sandbox._available:
                raise RuntimeError('Sandy v0.9996 no está disponible')
        except (FileNotFoundError, RuntimeError) as exc:
            return {'project_id':project_id,'status':'planned','execution_status':'blocked',
                    'preflight':f'Sandy no disponible; ejecución fail-closed: {exc}',
                    'workspace':str(ws),'history':[],'tasks':engine.brain.tasks(project_id),
                    'quality_decisions':[]}
        preflight = sandbox.preflight(ws,timeout=15)
        if not preflight.success:
            return {'project_id':project_id,'status':'planned','execution_status':'blocked',
                    'preflight':f'Sandy preflight falló ({preflight.sandy_exit_code}): {preflight.stderr[-1200:]}',
                    'workspace':str(ws),'history':[],'tasks':engine.brain.tasks(project_id),
                    'quality_decisions':[]}
        model = FreeLLMAPIAdapter()
        adapters = {
            'pm': model, 'architect': model, 'ux': model,
            'developer': SandyDeveloperAdapter(sandbox=sandbox, model=model),
            'qa': SandyQAAdapter(sandbox=sandbox),
            'devops': model,
        }
    engine.adapters = adapters
    history = engine.run_controlled(project_id, max_steps=max_steps)
    project = engine.brain.get_project(project_id)
    return {
        'project_id': project_id,
        'status': project['status'],
        'execution_status': 'completed' if project['status'] == 'done' else project['status'],
        'workspace': str(ws),
        'history': history,
        'tasks': engine.brain.tasks(project_id),
        'quality_decisions': engine.decisions(project_id),
    }


def resume_real_task(project_id: str, *, root=None, max_steps=30):
    """Resume un proyecto existente con los adaptadores fail-closed configurados."""
    root = Path(root).resolve() if root is not None else ROOT
    context_error = SandySandboxAdapter.execution_context_error()
    if context_error:
        engine = AutonomousEngine(root, adapters={})
        project = engine.brain.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        return {'project_id':project_id,'status':project['status'],'execution_status':'blocked',
                'preflight':context_error,'history':[],'tasks':engine.brain.tasks(project_id),
                'quality_decisions':engine.decisions(project_id)}
    engine = AutonomousEngine(root)
    project = engine.brain.get_project(project_id)
    if project is None:
        raise KeyError(project_id)
    developer = engine.adapters.get('developer')
    sandbox = getattr(developer, 'sandbox', None)
    workspace = root / 'workspaces' / project_id
    if sandbox is None or not sandbox._available:
        return {'project_id':project_id,'status':project['status'],'execution_status':'blocked',
                'preflight':'Sandy no disponible; ejecución fail-closed','history':[],
                'tasks':engine.brain.tasks(project_id),'quality_decisions':engine.decisions(project_id)}
    preflight = sandbox.preflight(workspace,timeout=15)
    if not preflight.success:
        return {'project_id':project_id,'status':project['status'],'execution_status':'blocked',
                'preflight':f'Sandy preflight falló ({preflight.sandy_exit_code}): {preflight.stderr[-1200:]}',
                'history':[],'tasks':engine.brain.tasks(project_id),
                'quality_decisions':engine.decisions(project_id)}
    history = engine.run_controlled(project_id, max_steps=max_steps)
    project = engine.brain.get_project(project_id)
    return {'project_id': project_id, 'status': project['status'],
            'execution_status': 'completed' if project['status']=='done' else project['status'],
            'history': history,
            'tasks': engine.brain.tasks(project_id), 'quality_decisions': engine.decisions(project_id)}

def main():
    parser=argparse.ArgumentParser(description='Exesoft AI Factory — núcleo persistente')
    parser.add_argument('--root', dest='root_dir', help='Directorio raíz (por defecto: ubicación del script)')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('create');p.add_argument('name');p.add_argument('request')
    p=sub.add_parser('plan');p.add_argument('project_id')
    p=sub.add_parser('status');p.add_argument('project_id')
    p=sub.add_parser('remember');p.add_argument('title');p.add_argument('body');p.add_argument('--source',required=True)
    p=sub.add_parser('recall');p.add_argument('query')
    p=sub.add_parser('verify');p.add_argument('--internal', action='store_true', help='Verificar circuito interno')
    p=sub.add_parser('run');p.add_argument('name');p.add_argument('request');p.add_argument('--max-steps',type=int,default=30)
    p=sub.add_parser('resume');p.add_argument('project_id');p.add_argument('--max-steps',type=int,default=30)
    p=sub.add_parser('tasks');p.add_argument('project_id')
    args=parser.parse_args()
    root_dir = Path(args.root_dir) if args.root_dir else ROOT

    if args.command == 'verify' and args.internal:
        success = run_verification(root_dir)
        print('Verificacion:', 'EXITOSA' if success else 'FALLIDA')
        return

    app=Orchestrator(root_dir)
    if args.command=='run': out=run_real_task(args.name,args.request,root=root_dir,max_steps=args.max_steps)
    elif args.command=='resume': out=resume_real_task(args.project_id,root=root_dir,max_steps=args.max_steps)
    elif args.command=='create': out={'project_id':app.create_project(args.name,args.request)}
    elif args.command=='plan': out=app.plan_skeleton(args.project_id)
    elif args.command=='status': out={'project':app.brain.get_project(args.project_id),'tasks':app.brain.tasks(args.project_id),'ready':app.ready_tasks(args.project_id)}
    elif args.command=='remember':out={'knowledge_id':app.brain.remember('note',args.title,args.body,args.source)}
    elif args.command=='tasks': out={'tasks': app.brain.tasks(args.project_id)}
    else:out=app.brain.recall(args.query)
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__=='__main__': main()
