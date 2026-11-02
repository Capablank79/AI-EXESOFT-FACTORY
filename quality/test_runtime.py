import tempfile
import unittest
from pathlib import Path
from engine.runtime import Runtime, Result


class FakeAdapter:
    def __init__(self, result):
        self.result=result
        self.calls=0
    def execute(self,task,project,context):
        self.calls+=1
        return self.result


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def build(self, adapters=None):
        rt=Runtime(self.root,adapters)
        pid=rt.orchestrator.create_project('Exesoft Factory','Crear motor de ejecución')
        rt.orchestrator.plan_skeleton(pid)
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
        ok=FakeAdapter(Result(True,'Listo'))
        rt,pid=self.build({'pm':ok,'architect':ok,'ux':ok,'developer':ok,'qa':ok,'devops':ok})
        rt.run(pid,max_steps=12)
        self.assertEqual(rt.brain.get_project(pid)['status'],'blocked')
        self.assertEqual([t for t in rt.brain.tasks(pid) if t['agent']=='qa'][0]['status'],'failed')

    def test_recover_interrupted_run(self):
        rt,pid=self.build()
        tid=rt.brain.tasks(pid)[0]['id']
        rt.brain.update_task(tid,'running',increment_attempts=True)
        revived=Runtime(self.root)
        self.assertEqual(revived.recover(pid),1)
        self.assertEqual(revived.brain.tasks(pid)[0]['status'],'pending')

    def test_resume_after_restart(self):
        ok=FakeAdapter(Result(True,'Listo',('verified',),True))
        # Updated for 6 agents
        adapters=dict.fromkeys(['pm','architect','ux','developer','qa','devops'],ok)
        rt,pid=self.build(adapters)
        rt.run(pid,max_steps=7)
        restarted=Runtime(self.root,adapters)
        self.assertEqual(restarted.run(pid,max_steps=12)[-1]['state'],'done')


if __name__ == '__main__': unittest.main()