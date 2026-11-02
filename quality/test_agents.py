import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from engine.agents import FreeLLMAPIAdapter, ClaudeCodeAdapter, make_prompt
from engine.agents import SandyDeveloperAdapter
from engine.agent_runtime import AgentRuntime
from engine.runtime import Result
from engine.sandy_adapter import SandYSandboxResult
from quality.evidence import verify_test_evidence

TASK={'agent':'pm','title':'Definir alcance'}
PROJECT={'name':'Exesoft','request':'Crear producto'}
CTX={'workspace':'.','knowledge':[{'kind':'decision','title':'Objetivo','body':'Solo local','source':'operador'}]}

class AgentTests(unittest.TestCase):
    def test_sandy_developer_applies_bounded_patch_and_records_test_evidence(self):
        payload = {'summary':'Implemented bounded change','files':[
            {'path':'app.py','content':'VALUE = 3\n'},
            {'path':'tests/test_app.py','content':'import unittest\nimport app\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(app.VALUE, 3)\n'}]}
        class Model:
            def execute(self, task, project, context):
                self.context=context
                return Result(True,json.dumps(payload))
        class Sandbox:
            def execute_argv(self, workspace, argv, timeout=None):
                self.argv=argv
                return SandYSandboxResult(True,'Ran 1 test','',0,25,'a'*64,'',0)
        with tempfile.TemporaryDirectory() as folder:
            model, sandbox = Model(), Sandbox()
            adapter = SandyDeveloperAdapter(sandbox=sandbox, model=model)
            result=adapter.execute({'agent':'developer','title':'Implement feature'},PROJECT,
                                   {'workspace':folder,'knowledge':[]})
            self.assertTrue(result.success, result.summary)
            self.assertIn('developer', model.context['instruction'].lower())
            self.assertEqual(sandbox.argv[0], __import__('sys').executable)
            self.assertTrue(verify_test_evidence(folder,result.evidence[0]))
            self.assertEqual((Path(folder)/'app.py').read_text(), 'VALUE = 3\n')

    def test_sandy_developer_rejects_path_traversal(self):
        payload = {'files':[{'path':'../escape.py','content':'x=1'}]}
        class Model:
            def execute(self, task, project, context): return Result(True,json.dumps(payload))
        with tempfile.TemporaryDirectory() as folder:
            adapter=SandyDeveloperAdapter(sandbox=object(),model=Model())
            result=adapter.execute({'agent':'developer','title':'Implement'},PROJECT,
                                   {'workspace':folder,'knowledge':[]})
            self.assertFalse(result.success)
            self.assertIn('Ruta de parche',result.summary)
            self.assertFalse((Path(folder).parent/'escape.py').exists())

    def test_context_prompt(self):
        prompt=make_prompt(TASK,PROJECT,CTX)
        self.assertIn('Solo local',prompt)
        self.assertIn('Definir alcance',prompt)

    def test_no_key_no_network(self):
        with patch.dict(os.environ,{},clear=True),patch('urllib.request.urlopen') as call:
            r=FreeLLMAPIAdapter().execute(TASK,PROJECT,CTX)
            self.assertFalse(r.success)
            call.assert_not_called()

    def test_router_success_mock(self):
        response=MagicMock()
        response.__enter__.return_value=response
        with patch('urllib.request.urlopen',return_value=response) as open_url,patch('json.load',return_value={'choices':[{'message':{'content':'Plan confirmado'}}]}):
            r=FreeLLMAPIAdapter(api_key='test').execute(TASK,PROJECT,CTX)
            self.assertTrue(r.success)
            self.assertIn('Plan confirmado',r.summary)
            self.assertEqual(open_url.call_args.kwargs['timeout'],90)

    def test_claude_missing_env_does_not_spawn(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True),patch('subprocess.run') as run:
            r=ClaudeCodeAdapter().execute(TASK,PROJECT,{'workspace':folder,'knowledge':[]})
            self.assertFalse(r.success)
            run.assert_not_called()

    def test_claude_planning_flags(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'ANTHROPIC_AUTH_TOKEN':'test','ANTHROPIC_BASE_URL':'http://127.0.0.1:3001'}),patch('subprocess.run') as run:
            run.return_value=MagicMock(returncode=0,stdout=json.dumps({'result':'Evaluación de arquitectura'}))
            r=ClaudeCodeAdapter().execute(TASK,PROJECT,{'workspace':folder,'knowledge':[]})
            self.assertTrue(r.success)
            argv=run.call_args.args[0]
            self.assertIn('plan',argv)
            self.assertIn('--disallowedTools',argv)
            self.assertFalse(run.call_args.kwargs['shell'])

    def test_agent_runtime_context_from_brain(self):
        class Spy:
            def execute(self,task,project,context):
                self.context=context
                return Result(True,'Plan')
        with tempfile.TemporaryDirectory() as folder:
            spy=Spy()
            runtime=AgentRuntime(folder,{'pm':spy})
            pid=runtime.orchestrator.create_project('Factory','Construir fábrica')
            runtime.orchestrator.plan_skeleton(pid)
            runtime.brain.remember('decision','Definir alcance','Mantener arquitectura local','operador',pid)
            result=runtime.step(pid)
            self.assertEqual(result['state'],'done')
            self.assertTrue(spy.context['knowledge'])
            # Knowledge now includes ODD feature doc; verify user knowledge still accessible
            found_local = any('local' in k.get('body', '') for k in spy.context['knowledge'])
            self.assertTrue(found_local, f"'local' not found in knowledge: {spy.context['knowledge']}")

if __name__=='__main__':unittest.main()
