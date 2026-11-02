"""Tests for updated evidence.py functionality."""
import unittest
import subprocess
import tempfile
from pathlib import Path
from quality.evidence import (run_tests, record_evidence, verify_test_evidence,
                              record_sandbox_test_evidence)
from engine.sandy_adapter import SandYSandboxResult


class EvidenceTests(unittest.TestCase):
    def test_sandy_evidence_requires_success_and_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            good = SandYSandboxResult(True, 'Ran 2 tests', '', 0, 10, 'a' * 64, '', 0)
            ref = record_sandbox_test_evidence(folder, ['python', '-m', 'unittest'], good)
            self.assertTrue(verify_test_evidence(folder, ref))
            failed = SandYSandboxResult(False, 'FAILED', '', 1, 10, 'b' * 64, 'executor_error', 1)
            failed_ref = record_sandbox_test_evidence(folder, ['python', '-m', 'unittest'], failed)
            self.assertFalse(verify_test_evidence(folder, failed_ref))

    def setUp(self):
        self.workspace = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_run_tests_creates_tests_dir(self):
        """Test Phase 1: run_tests creates tests/ directory if missing."""
        # Ensure no tests/ exists initially
        tests_dir = Path(self.workspace) / 'tests'
        self.assertFalse(tests_dir.exists())
        artifact = run_tests(self.workspace)
        self.assertTrue(tests_dir.exists())

    def test_run_tests_without_tests(self):
        """Test that run_tests fails gracefully with empty workspace."""
        # empty workspace
        artifact = run_tests(self.workspace, partial_ok=True)
        self.assertIn('status', artifact)
        self.assertEqual(artifact['kind'], 'test')

    def test_record_and_verify_evidence(self):
        """An empty suite is integrity-valid but cannot establish a QA pass."""
        artifact = run_tests(self.workspace, partial_ok=True)
        rel_path = record_evidence(self.workspace, artifact)
        verified = verify_test_evidence(self.workspace, rel_path)
        self.assertFalse(verified, f"Empty test run unexpectedly qualified: {rel_path}")

    def test_verify_evidence_rejects_malformed(self):
        """Test that malformed evidence is rejected."""
        test_file = Path(self.workspace) / 'test_bad.json'
        test_file.write_text('{invalid json}')
        # Should return False
        result = verify_test_evidence(self.workspace, 'test_bad.json')
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
