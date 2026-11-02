import json
import tempfile
import unittest
from pathlib import Path
from engine.autonomous import AutonomousEngine
from engine.runtime import Result
from quality.evidence import record_evidence, run_tests

class Stub:
    def __init__(self, result): self.result=result; self.calls=0
    def execute(self, task, project, context): self.calls+=1; return self.result

class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.ok=Stub(Result(True,'ok'))
        # QA adapter needs evidence and approval=True
        self.qa=Stub(Result(True,'qa revisado',approved=True,evidence=('declarada',)))
        # Updated for 6 agents: pm, architect, ux, developer, qa, devops
        self.engine=AutonomousEngine(self.root,adapters={'pm':self.ok,'architect':self.ok,'ux':self.ok,'developer':self.ok,'qa':self.qa,'devops':self.ok})
        self.pid=self.engine.orchestrator.create_project('Proyecto','Solicitud válida')
        self.engine.orchestrator.plan_skeleton(self.pid)

    def tearDown(self): self.tmp.cleanup()

    def _advance_to_qa(self):
        # Need 5 steps to reach QA (pm, architect, ux, developer, then qa)
        # But QA requires evidence - so advance to just before QA
        for _ in range(4):  # pm, architect, ux, developer
            result = self.engine.step(self.pid)
            # Some agents don't have adapters registered
            if result['state'] not in ('done', 'pending'):
                break

    def test_all_modules_import(self): self.assertIsNotNone(self.engine.brain)

    def test_no_qa_without_evidence(self):
        self._advance_to_qa()
        self.assertEqual(self.engine.step(self.pid)['state'],'pending')
        self.assertEqual(self.qa.calls,0)
        self.assertFalse(self.engine.decisions(self.pid)[0]['approved'])

    def test_reject_fake_evidence(self):
        with self.assertRaises(ValueError):
            self.engine.register_test_evidence(self.pid,'.exesoft/evidence/missing.json')

    def _evidence(self):
        workspace=self.root/'workspaces'/self.pid
        tests=workspace/'tests';tests.mkdir()
        (tests/'test_math.py').write_text('import unittest\nclass TestMath(unittest.TestCase):\n def test_one(self):self.assertEqual(1,1)\n',encoding='utf8')
        return record_evidence(workspace,run_tests(workspace))

    def test_qa_with_evidence_and_approval(self):
        ref=self._evidence()
        self.engine.register_test_evidence(self.pid,ref)
        self._advance_to_qa()
        self.assertEqual(self.engine.step(self.pid)['state'],'done')
        self.assertEqual(self.engine.decisions(self.pid)[0]['approved'],1)
        self.assertEqual(self.engine.step(self.pid)['state'],'done')
        self.assertEqual(self.engine.step(self.pid)['state'],'done') # mark project done
        self.assertEqual(self.engine.brain.get_project(self.pid)['status'],'done')

    def test_qa_declined_even_with_evidence(self):
        self.engine.register_test_evidence(self.pid,self._evidence())
        self.qa.result=Result(True,'aparentemente aprobado',approved=False)
        self._advance_to_qa()
        self.assertEqual(self.engine.step(self.pid)['state'],'pending')
        self.assertFalse(self.engine.decisions(self.pid)[0]['approved'])

    def test_tampered_artifact_after_registration(self):
        ref=self._evidence()
        self.engine.register_test_evidence(self.pid,ref)
        p=self.root/'workspaces'/self.pid/ref
        data=json.loads(p.read_text());data['output']='alterado';p.write_text(json.dumps(data))
        self._advance_to_qa()
        self.assertEqual(self.engine.step(self.pid)['state'],'pending')
        self.assertEqual(self.qa.calls,0)

    def test_interrupted_requires_manual_review(self):
        self.engine.brain.update_task(self.engine.brain.tasks(self.pid)[0]['id'],'running')
        self.assertEqual(self.engine.run_controlled(self.pid)[0]['state'],'manual_recovery_required')
        self.assertEqual(self.ok.calls,0)

    def test_retry_has_no_busy_loop(self):
        failing=Stub(Result(False,'fallo externo'))
        self.engine.adapters['pm']=failing
        result=self.engine.run_controlled(self.pid,max_steps=10)
        self.assertEqual(len(result),1)
        self.assertEqual(failing.calls,1)

    def test_audit_persists_on_restart(self):
        self._advance_to_qa();self.engine.step(self.pid)
        restored=AutonomousEngine(self.root,adapters={})
        self.assertEqual(len(restored.decisions(self.pid)),1)

if __name__=='__main__':unittest.main()