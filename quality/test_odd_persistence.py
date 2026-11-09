import tempfile
import unittest
from pathlib import Path

from brain.store import Brain
from engine.odd import ODDEngine, FeatureDocument
from engine.orchestrator import Orchestrator
from engine.runtime import LegacySQLiteRuntime as Runtime, Result


class OddPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.orchestrator = Orchestrator(self.root, backend='sqlite')
        self.pid = self.orchestrator.create_project('sample', 'sample request')

    def test_plan_document_is_complete_current_and_deduplicated(self):
        self.orchestrator.plan_skeleton(self.pid)
        first = self.orchestrator.get_odd_feature(self.pid)
        self.assertEqual((len(first.specs), len(first.tasks)), (6, 6))
        self.assertEqual(len({s.id for s in first.specs}), 6)
        self.assertEqual(len({t.id for t in first.tasks}), 6)
        second = self.orchestrator.get_odd_feature(self.pid)
        self.assertEqual(second.to_json(), first.to_json())
        with self.orchestrator.brain.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM knowledge WHERE project_id=? AND kind='odd_spec'", (self.pid,)).fetchone()[0]
        self.assertGreaterEqual(count, 1)
        # Reads and repeated status updates do not append duplicate snapshots.
        ref = self.orchestrator.brain.odd_task_for_brain(self.pid, self.orchestrator.brain.tasks(self.pid)[0]['id'])
        before = count
        self.orchestrator.odd.update_task_status(self.pid, ref, 'pending')
        with self.orchestrator.brain.connect() as db:
            after = db.execute("SELECT COUNT(*) FROM knowledge WHERE project_id=? AND kind='odd_spec'", (self.pid,)).fetchone()[0]
        self.assertEqual(after, before)

    def test_legacy_fragmented_snapshots_are_recovered_without_deleting_history(self):
        for index in range(1, 4):
            fragment = FeatureDocument(self.pid, 'legacy', 'legacy request')
            spec = fragment.add_spec(f'Spec {index}', f'Description {index}', [f'AC {index}'])
            task = fragment.add_task(spec.id, f'agent{index}', f'Task {index}')
            self.orchestrator.brain.remember('odd_spec', 'Feature: legacy', fragment.to_json(), 'legacy', self.pid)
        loaded = self.orchestrator.odd.load_feature(self.pid)
        self.assertEqual([s.title for s in loaded.specs], ['Spec 1', 'Spec 2', 'Spec 3'])
        self.assertEqual([t.description for t in loaded.tasks], ['Task 1', 'Task 2', 'Task 3'])
        self.assertEqual(len({t.id for t in loaded.tasks}), 3)
        with self.orchestrator.brain.connect() as db:
            self.assertGreaterEqual(db.execute("SELECT COUNT(*) FROM knowledge WHERE project_id=? AND kind='odd_spec'", (self.pid,)).fetchone()[0], 3)
        self.assertEqual(len(self.orchestrator.odd.load_feature(self.pid).tasks), 3)

    def test_runtime_task_transitions_sync_to_odd(self):
        self.orchestrator.plan_skeleton(self.pid)
        runtime = Runtime(self.root, adapters={'pm': _SuccessAdapter()})
        outcome = runtime.step(self.pid)
        self.assertEqual(outcome['state'], 'done')
        task_id = self.orchestrator.brain.odd_task_for_brain(self.pid, outcome['task'])
        odd_task = next(t for t in runtime.odd.load_feature(self.pid).tasks if t.id == task_id)
        self.assertEqual(odd_task.status, 'done')


class _SuccessAdapter:
    def execute(self, task, project, context):
        return Result(True, 'completed')


if __name__ == '__main__':
    unittest.main()
