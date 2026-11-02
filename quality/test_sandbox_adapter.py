"""Tests for Windows Sandbox Adapter."""
import unittest
import tempfile
from pathlib import Path
from engine.sandbox_adapter import WindowsSandboxAdapter, SandboxExecutionPolicy
from engine.execution_sandbox import ExecutionDecision


class SandboxAdapterTests(unittest.TestCase):
    """Tests for Windows Sandbox Adapter."""

    def test_sandbox_not_available_without_feature(self):
        """Test that adapter detects missing Windows Sandbox feature."""
        # On systems without the feature, this should raise RuntimeError
        try:
            adapter = WindowsSandboxAdapter()
            # If it doesn't raise, verify it's actually available
            self.assertTrue(hasattr(adapter, 'memory_mb'))
        except RuntimeError as e:
            # Expected on systems without the feature
            self.assertIn("Windows Sandbox", str(e))

    def test_execution_policy_without_sandbox(self):
        """Test ExecutionDecision when no sandbox is available."""
        policy = SandboxExecutionPolicy(adapter=None)

        # Untrusted code should be denied
        decision = policy.evaluate(trusted_code=False)
        self.assertFalse(decision.allowed)
        self.assertIn("aislamiento", decision.reason)

        # Trusted code without sandbox should be allowed (fallback for internal tools)
        decision = policy.evaluate(trusted_code=True)
        self.assertTrue(decision.allowed)

        # Requested network should be denied
        decision = policy.evaluate(requested_network=True)
        self.assertFalse(decision.allowed)

        # Requested host paths should be denied
        decision = policy.evaluate(requested_host_paths=True)
        self.assertFalse(decision.allowed)

    def test_execution_policy_with_sandbox(self):
        """Test ExecutionDecision when sandbox is available (mock)."""
        # Create a mock adapter
        class MockAdapter:
            pass

        policy = SandboxExecutionPolicy(adapter=MockAdapter())

        # Untrusted code should be allowed with sandbox
        decision = policy.evaluate(trusted_code=False)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.isolation, 'windows_sandbox')

        # Trusted code should be allowed
        decision = policy.evaluate(trusted_code=True)
        self.assertTrue(decision.allowed)

        # Network request should be allowed
        decision = policy.evaluate(requested_network=True)
        self.assertTrue(decision.allowed)

        # Host paths should still be denied
        decision = policy.evaluate(requested_host_paths=True)
        self.assertFalse(decision.allowed)

    def test_policy_require_raises(self):
        """Test that require() raises on denied decisions."""
        policy = SandboxExecutionPolicy(adapter=None)

        from engine.execution_sandbox import ExecutionDenied
        with self.assertRaises(ExecutionDenied):
            policy.require(trusted_code=False)


if __name__ == '__main__':
    unittest.main()