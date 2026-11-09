"""End-to-end test for EXESOFT AI FACTORY autonomous workflow.

This test creates a real project and runs the full pipeline:
Solicitud → PM → Architect → UX → Developer → QA → Delivery

Tests:
- Project creation with persistent Brain
- Plan skeleton with 6 tasks (updated for UX)
- Execution with mock adapters (simulating Claude Code via FreeLLMAPI)
- Quality Gate with evidence
- Recovery after interruption
- Final state: project done with all tasks completed
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())

from engine.autonomous import AutonomousEngine
from engine.runtime import Result
from engine.agents import SandyDeveloperAdapter, SandyQAAdapter
from engine.sandy_adapter import SandYSandboxResult
from factory import run_real_task, run_verification
from quality.evidence import run_tests, record_evidence

TENANT_ID = 'b66f93cd-2f3a-4215-a2ac-93a59a84b4d2'


class E2EFactoyTests(unittest.TestCase):
    """Real end-to-end tests of the factory pipeline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = AutonomousEngine(self.root)

    def _create_real_project(self, name="Test Factory", request="Build a simple Python module"):
        """Create a project with a real workspace."""
        pid = uuid4().hex[:12]
        workspace = self.root / 'workspaces' / pid
        workspace.mkdir(parents=True)
        (workspace / 'request.md').write_text(f"# {name}\n\n{request}\n", encoding='utf-8')

        # Persist in Brain
        self.engine.brain.add_project(pid, name, request)
        # Use orchestrator for planning
        self.engine.orchestrator.plan_skeleton(pid)
        return pid, workspace

    def test_project_creation_and_persistence(self):
        """Test 1: Project creation persists to SQLite Brain."""
        pid, ws = self._create_real_project()
        project = self.engine.brain.get_project(pid)
        self.assertIsNotNone(project)
        self.assertEqual(project['name'], 'Test Factory')
        self.assertEqual(project['status'], 'planned')
        tasks = self.engine.brain.tasks(pid)
        # Updated for 6 agents: pm, architect, ux, developer, qa, devops
        self.assertEqual(len(tasks), 6)

    def test_real_task_entrypoint_runs_six_roles_and_registers_sandy_evidence(self):
        class Model:
            def execute(self, task, project, context):
                if task['agent'] == 'developer':
                    response = {'summary':'Added app module','files':[
                        {'path':'app.py','content':'VALUE = 7\n'},
                        {'path':'tests/test_app.py','content':'import unittest\nimport app\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(app.VALUE, 7)\n'}]}
                    return Result(True,json.dumps(response))
                return Result(True,task['agent']+' completed')

        class Sandbox:
            def execute_argv(self, workspace, argv, timeout=None):
                self.last_argv = argv
                return SandYSandboxResult(True,'Ran 1 test','',0,15,'c'*64,'',0)

        model, sandbox = Model(), Sandbox()
        adapters = {'pm':model, 'architect':model, 'ux':model,
                    'developer':SandyDeveloperAdapter(sandbox=sandbox,model=model),
                    'qa':SandyQAAdapter(sandbox=sandbox), 'devops':model}
        result = run_real_task('Entry Point Test','Implement a tiny module',
                               root=self.root,adapters=adapters,tenant_id=TENANT_ID)
        self.assertEqual(result['status'],'done')
        self.assertEqual([t['status'] for t in result['tasks']],['done']*6)
        self.assertTrue(result['quality_decisions'][0]['approved'])
        self.assertEqual(sandbox.last_argv[0], __import__('sys').executable)

    def test_internal_verification_uses_created_project_id(self):
        self.assertTrue(run_verification(self.root, tenant_id=TENANT_ID))

    def test_real_task_stops_before_model_when_sandy_preflight_fails(self):
        class Sandbox:
            _available=True
            def execute_argv(self, workspace, argv, timeout=None):
                self.argv=argv
                return SandYSandboxResult(False,'','registry grant setup failed',129,12,'d'*64,'executor_error',129)
            def preflight(self, workspace, timeout=15):
                return self.execute_argv(workspace,[__import__('sys').executable,'-I','-c','pass'],timeout)
        sandbox=Sandbox()
        with patch('engine.sandy_adapter.SandySandboxAdapter.execution_context_error',return_value=''):
            result=run_real_task('Blocked Sandy','Build a module',root=self.root,sandbox=sandbox,
                                 tenant_id=TENANT_ID)
        self.assertEqual(result['execution_status'],'blocked')
        self.assertEqual(result['status'],'planned')
        self.assertIn('registry grant setup failed',result['preflight'])
        self.assertEqual(sandbox.argv[-1],'pass')
        self.assertTrue(all(task['status']=='pending' for task in result['tasks']))

    def test_full_pipeline_with_evidence(self):
        """Test 2: Full pipeline executes with evidence."""
        pid, ws = self._create_real_project()

        # Create a real test in the workspace
        tests_dir = ws / 'tests'
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / 'test_example.py').write_text(
            'import unittest\nclass T(unittest.TestCase):\n'
            '    def test_ok(self): self.assertEqual(2, 2)\n',
            encoding='utf-8'
        )

        # Register evidence
        artifact = run_tests(ws)
        rel_path = record_evidence(ws, artifact)
        self.engine.register_test_evidence(pid, rel_path)

        # Create a mock adapter that simulates successful execution
        class MockAdapter:
            def execute(self, task, project, context):
                if task['agent'] == 'pm':
                    return Result(True, 'Scope defined: simple module', ())
                elif task['agent'] in ['architect', 'ux']:
                    return Result(True, 'UX/Architecture approved', ())
                elif task['agent'] == 'developer':
                    return Result(True, 'Implemented: module.py', ())
                elif task['agent'] == 'qa':
                    return Result(True, 'Tests pass', (rel_path,), approved=True)
                elif task['agent'] == 'devops':
                    return Result(True, 'Delivery ready', ())
                return Result(False, f'Unknown agent: {task["agent"]}')

        # Register adapters for all 6 agents
        for agent in ['pm', 'architect', 'ux', 'developer', 'qa', 'devops']:
            self.engine.adapters[agent] = MockAdapter()

        # Run the pipeline
        history = self.engine.run_controlled(pid, max_steps=20)
        self.assertGreater(len(history), 0)

        # Verify final state
        project = self.engine.brain.get_project(pid)
        self.assertEqual(project['status'], 'done')
        tasks = self.engine.brain.tasks(pid)
        self.assertTrue(all(t['status'] == 'done' for t in tasks))

    def test_recovery_after_interruption(self):
        """Test 3: Factory recovers after interruption."""
        pid, ws = self._create_real_project()

        # Simulate interruption: mark a task as running
        tasks = self.engine.brain.tasks(pid)
        self.engine.brain.update_task(tasks[0]['id'], 'running', increment_attempts=True)

        # Recover
        count = self.engine.recover(pid)
        self.assertEqual(count, 1)

        # Verify task is back to pending
        tasks = self.engine.brain.tasks(pid)
        self.assertEqual(tasks[0]['status'], 'pending')

    def test_quality_gate_blocks_without_evidence(self):
        """Test 4: QA task blocked without evidence."""
        pid, ws = self._create_real_project()

        # Set previous tasks to completed so QA is next (now 5 tasks: pm, architect, ux, developer)
        tasks = self.engine.brain.tasks(pid)
        for task in tasks:
            if task['agent'] in ['pm', 'architect', 'ux', 'developer']:
                self.engine.brain.update_task(task['id'], 'done')

        # No evidence registered
        class MockAdapter:
            def execute(self, task, project, context):
                return Result(True, 'Done', (), approved=True)

        self.engine.adapters['qa'] = MockAdapter()

        # Run pipeline - should block at QA
        history = self.engine.run_controlled(pid, max_steps=10)
        # Check that QA was attempted and blocked
        self.assertTrue(any(h['state'] in ('blocked', 'pending') for h in history))

    def test_retry_limits(self):
        """Test 5: Tasks respect max_attempts limit."""
        pid, ws = self._create_real_project()

        # Create failing adapter
        class FailingAdapter:
            def execute(self, task, project, context):
                return Result(False, 'Persistent failure')

        self.engine.adapters['pm'] = FailingAdapter()

        # Run multiple times to exhaust attempts (run_controlled doesn't retry on pending)
        for _ in range(4):
            self.engine.run_controlled(pid, max_steps=10)

        tasks = self.engine.brain.tasks(pid)
        # PM task should be failed after exhausting attempts
        pm_task = [t for t in tasks if t['agent'] == 'pm'][0]
        self.assertEqual(pm_task['status'], 'failed', f"PM task status: {pm_task['status']}")

    def test_knowledge_persistence_across_restarts(self):
        """Test 6: Knowledge persists across engine restarts."""
        pid, ws = self._create_real_project()

        # Remember something
        self.engine.brain.remember('decision', 'Test Decision', 'We chose Python', 'pm', project_id=pid)

        # Create a new engine (simulating restart)
        new_engine = AutonomousEngine(self.root)
        results = new_engine.brain.recall('Test Decision', project_id=pid)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Test Decision')


if __name__ == '__main__':
    unittest.main()
