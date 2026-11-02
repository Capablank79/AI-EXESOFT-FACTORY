import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from quality.evidence import file_evidence, verify_file, run_tests, record_evidence, verify_test_evidence
from engine.quality_gate import QualityGate

class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.ws=self.root/'workspaces'/'p1'
        self.ws.mkdir(parents=True)
        (self.ws/'tests').mkdir()
    def put_test(self,content):
        (self.ws/'tests'/'test_example.py').write_text(content,encoding='utf-8')
    def test_file_evidence_verifiable_and_tamper(self):
        (self.ws/'a.txt').write_text('hola')
        evidence=file_evidence(self.ws,'a.txt')
        self.assertTrue(verify_file(self.ws,evidence))
        (self.ws/'a.txt').write_text('cambiado')
        self.assertFalse(verify_file(self.ws,evidence))
    def test_traversal_rejected(self):
        (self.root/'outside').write_text('s')
        with self.assertRaises(ValueError):file_evidence(self.ws,'../../outside')
    def test_executable_tests_pass(self):
        self.put_test('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertEqual(2,2)\n')
        art=run_tests(self.ws)
        self.assertEqual(art['status'],'passed')
        ref=record_evidence(self.ws,art)
        self.assertTrue(verify_test_evidence(self.ws,ref))
        self.assertTrue(QualityGate(self.root).evaluate('p1',SimpleNamespace(success=True),[ref])['approved'])
    def test_executable_tests_fail(self):
        self.put_test('import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail("broken")\n')
        art=run_tests(self.ws)
        ref=record_evidence(self.ws,art)
        self.assertEqual(art['status'],'failed')
        self.assertFalse(verify_test_evidence(self.ws,ref))
        self.assertFalse(QualityGate(self.root).evaluate('p1',SimpleNamespace(success=True),[ref])['approved'])
    def test_partial_ok_cannot_approve_failed_test_run(self):
        self.put_test('import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail("repro partial bypass")\n')
        artifact=run_tests(self.ws,partial_ok=True)
        ref=record_evidence(self.ws,artifact)
        self.assertEqual(artifact['status'],'failed')
        self.assertNotEqual(artifact['returncode'],0)
        self.assertFalse(verify_test_evidence(self.ws,ref))
        decision=QualityGate(self.root).evaluate('p1',SimpleNamespace(success=True),[ref])
        self.assertFalse(decision['approved'])
    def test_empty_suite_cannot_approve(self):
        ref=record_evidence(self.ws,run_tests(self.ws,partial_ok=True))
        self.assertFalse(QualityGate(self.root).evaluate('p1',SimpleNamespace(success=True),[ref])['approved'])
    def test_tampered_test_record_rejected(self):
        self.put_test('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        ref=record_evidence(self.ws,run_tests(self.ws))
        p=self.ws/ref
        data=json.loads(p.read_text());data['output']='inventado'
        p.write_text(json.dumps(data))
        self.assertFalse(verify_test_evidence(self.ws,ref))
    def test_model_claim_does_not_approve(self):
        self.assertFalse(QualityGate(self.root).evaluate('p1',SimpleNamespace(success=True),[])['approved'])
    def test_failed_task_does_not_approve(self):
        self.assertFalse(QualityGate(self.root).evaluate('p1',SimpleNamespace(success=False),[])['approved'])
    def test_git_init_and_status(self):
        from quality.git_isolation import initialize_workspace,git_status
        initialize_workspace(self.ws)
        (self.ws/'foo.txt').write_text('ok')
        self.assertIn('foo.txt',git_status(self.ws))
        with self.assertRaises(ValueError):initialize_workspace(self.ws)

if __name__=='__main__':unittest.main()
