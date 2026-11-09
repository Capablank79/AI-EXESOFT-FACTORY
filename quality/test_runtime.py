import tempfile
import unittest
import uuid
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from engine.runtime import Runtime, Result
from plan_approval_test_support import approve_test_plan
from quality.evidence import record_evidence

TENANT_ID = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


class FakeAdapter:
    def __init__(self, result, evidence_agents=('developer', 'qa')):
        self.result=result
        self.calls=0
        self.evidence_agents=set(evidence_agents)
    def execute(self,task,project,context):
        self.calls+=1
        evidence=()
        if task['agent'] in self.evidence_agents and self.result.evidence:
            output='Ran 1 test'
            evidence=(record_evidence(context['workspace'], {
                'schema':'exesoft.quality.test.v1', 'id':uuid.uuid4().hex,
                'kind':'test', 'runner':'sandy', 'status':'passed',
                'returncode':0, 'sandy_exit_code':0, 'output':output,
                'output_sha256':hashlib.sha256(output.encode()).hexdigest(),
                'tests_run':1, 'result_sha256':'0'*64, 'partial_ok':False,
                'timestamp':datetime.now(timezone.utc).isoformat(),
            }),)
        return Result(self.result.success, self.result.summary, evidence, self.result.approved)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def build(self, adapters=None):
        rt=Runtime(self.root,adapters,tenant_id=TENANT_ID)
        pid=rt.orchestrator.create_project('Exesoft Factory','Crear motor de ejecución')
        rt.orchestrator.plan_skeleton(pid)
        approve_test_plan(TENANT_ID, pid)
        return rt,pid

    def test_no_adapter_blocks_and_preserves_evidence(self):
        rt,pid=self.build()
        history=rt.run(pid,max_steps=10)
        self.assertEqual(rt.brain.get_project(pid)['status'],'blocked')
        # Tasks will block when no adapter - count actual runs
        runs = len(rt.runs(pid))
        self.assertGreater(runs, 0)  # At least some tasks were run
        self.assertEqual(history[-1]['state'],'blocked')

    def test_successful_pipeline_with_qa_evidence(self):
        adapter=FakeAdapter(Result(True,'Verificado',('evidence:sha256:abc',),True))
        # Updated for 6 agents: pm, architect, ux, developer, qa, devops
        rt,pid=self.build(dict.fromkeys(['pm','architect','ux','developer','qa','devops'],adapter))
        history=rt.run(pid,max_steps=12)
        self.assertEqual(history[-1]['state'],'done')
        self.assertEqual(len(rt.runs(pid)),6)
        self.assertEqual(adapter.calls,6)

    def test_qa_cannot_self_approve_without_evidence(self):
        ok=FakeAdapter(Result(True,'Listo',('developer test evidence',),True),
                       evidence_agents=('developer',))
        rt,pid=self.build({'pm':ok,'architect':ok,'ux':ok,'developer':ok,'qa':ok,'devops':ok})
        rt.run(pid,max_steps=12)
        self.assertEqual(rt.brain.get_project(pid)['status'],'blocked')
        self.assertEqual([t for t in rt.brain.tasks(pid) if t['agent']=='qa'][0]['status'],'failed')

    def test_recover_interrupted_run(self):
        rt,pid=self.build()
        tid=rt.brain.tasks(pid)[0]['id']
        rt.brain.update_task(tid,'running',increment_attempts=True)
        revived=Runtime(self.root,tenant_id=TENANT_ID)
        self.assertEqual(revived.recover(pid),1)
        self.assertEqual(revived.brain.tasks(pid)[0]['status'],'pending')

    def test_resume_after_restart(self):
        ok=FakeAdapter(Result(True,'Listo',('verified',),True))
        # Updated for 6 agents
        adapters=dict.fromkeys(['pm','architect','ux','developer','qa','devops'],ok)
        rt,pid=self.build(adapters)
        rt.run(pid,max_steps=7)
        restarted=Runtime(self.root,adapters,tenant_id=TENANT_ID)
        self.assertEqual(restarted.run(pid,max_steps=12)[-1]['state'],'done')


if __name__ == '__main__': unittest.main()
