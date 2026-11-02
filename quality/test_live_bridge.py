import json
import tempfile
import unittest
from unittest.mock import patch
from engine.live_bridge import supervised_cycle

class Reply:
    def __init__(self, model='@cf/openai/gpt-oss-120b'):
        self.content=json.dumps({'model':model,'choices':[{'message':{'content':'Integración completada y persistida.'}}]}).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self, n=-1):return self.content if n<0 else self.content[:n]

class LiveBridgeTests(unittest.TestCase):
    def test_success_persists_run_and_knowledge(self):
        with tempfile.TemporaryDirectory() as root, patch('urllib.request.urlopen', return_value=Reply()) as mock:
            report=supervised_cycle(root, 'fake-key')
            self.assertEqual(report.state, 'succeeded')
            self.assertIsInstance(report.knowledge_id, int)
            self.assertTrue(report.run_id)
            self.assertEqual(mock.call_count,1)
            req=mock.call_args.args[0]
            self.assertEqual(json.loads(req.data)['model'],'gpt-oss-120b')
            from brain.store import Brain
            brain=Brain(root+'/brain/brain.db')
            with brain.connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_runs WHERE project_id=?',(report.project_id,)).fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT body FROM knowledge WHERE id=?',(report.knowledge_id,)).fetchone()[0], report.summary)

    def test_rejects_invalid_key_no_request(self):
        with tempfile.TemporaryDirectory() as root, patch('urllib.request.urlopen') as mock:
            with self.assertRaises(ValueError):supervised_cycle(root,'abc\nunsafe')
            mock.assert_not_called()

    def test_failure_keeps_auditable_run_but_no_success_knowledge(self):
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as root, patch('urllib.request.urlopen',side_effect=HTTPError('url',502,'bad gateway',{},None)):
            report=supervised_cycle(root,'fake-key')
            self.assertEqual(report.state,'failed')
            self.assertIsNone(report.knowledge_id)
            self.assertTrue(report.run_id)

    def test_only_known_model(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):supervised_cycle(root,'fake-key',model='auto')

if __name__=='__main__':unittest.main()
