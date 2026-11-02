import tempfile
import unittest
from pathlib import Path
from brain.store import Brain
from engine.orchestrator import Orchestrator

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.app=Orchestrator(self.root)
    def tearDown(self):self.tmp.cleanup()

    def test_project_plan_and_recovery(self):
        pid=self.app.create_project('Factory','Construir Brain')
        tasks=self.app.plan_skeleton(pid)
        # Updated for 6 agents
        self.assertEqual(len(tasks),6)
        # First task (pm) ready
        self.assertEqual(len(self.app.ready_tasks(pid)),1)
        # Reload and verify count
        self.assertEqual(len(Orchestrator(self.root).brain.tasks(pid)),6)
        # Mark pm done -> architect should be next
        self.app.brain.update_task(tasks[0]['id'],'done')
        self.assertEqual(len(self.app.ready_tasks(pid)),1)
        self.assertEqual(self.app.ready_tasks(pid)[0]['agent'],'architect')

    def test_knowledge_fts(self):
        self.app.brain.remember('decision','Memoria SQLite','FTS5 indexa arquitectura','operador')
        self.assertEqual(self.app.brain.recall('arquitectura')[0]['title'],'Memoria SQLite')

    def test_reject_duplicate_plan(self):
        pid=self.app.create_project('Factory','Requisitos')
        self.app.plan_skeleton(pid)
        with self.assertRaises(ValueError):self.app.plan_skeleton(pid)

if __name__=='__main__':unittest.main()