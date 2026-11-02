"""Tests only use trusted locally generated commits; never execute untrusted code."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.execution_sandbox import (ExecutionDenied, ExecutionPolicy,
                                      create_isolated_worktree, worktree_status)


def git(*args):
    p = subprocess.run(['git', *map(str,args)], capture_output=True, text=True, timeout=10,
                       env={**os.environ, 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':os.devnull})
    if p.returncode:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()


class SandboxTests(unittest.TestCase):
    def test_no_backend_denies_untrusted(self):
        self.assertFalse(ExecutionPolicy().evaluate().allowed)

    def test_attestation_flag_does_not_bypass(self):
        self.assertFalse(ExecutionPolicy().evaluate(trusted_code=True,sandbox_attested=True).allowed)

    def test_network_denied(self):
        self.assertIn('Red',ExecutionPolicy().evaluate(requested_network=True).reason)

    def test_host_paths_denied(self):
        self.assertIn('host',ExecutionPolicy().evaluate(requested_host_paths=True).reason)

    def test_require_fails_closed(self):
        with self.assertRaises(ExecutionDenied):
            ExecutionPolicy().require(trusted_code=True)

    def test_worktree_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=root/'repo'; repo.mkdir(); dest=root/'branches'; dest.mkdir()
            git('init','-q',repo)
            (repo/'README.txt').write_text('base',encoding='utf-8')
            git('-C',repo,'add','README.txt')
            git('-C',repo,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-q','-m','initial')
            path=create_isolated_worktree(repo,dest,'abc_123')
            self.assertEqual((path/'README.txt').read_text(), 'base')
            self.assertEqual(worktree_status(path),'')
            (path/'README.txt').write_text('changed')
            self.assertIn('README.txt',worktree_status(path))
            self.assertEqual((repo/'README.txt').read_text(),'base')
            with self.assertRaises(FileExistsError):
                create_isolated_worktree(repo,dest,'abc_123')
            git('-C',repo,'worktree','remove','--force',path)

    def test_invalid_task_id_rejected(self):
        with self.assertRaises(ValueError):
            create_isolated_worktree('.','.', '../bad')

    def test_nested_root_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); repo=base/'source'; repo.mkdir(); destination=repo/'child'; destination.mkdir()
            with self.assertRaises(ValueError):
                create_isolated_worktree(repo,destination,'valid')

if __name__ == '__main__':
    unittest.main()
