"""Explicit native Sandy checks. Safe to import; ACL-changing checks run only as a script."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from engine.sandy_adapter import SandySandboxAdapter


def _status(executable):
    result = subprocess.run([str(executable), '--status', '--json'], capture_output=True,
                            text=True, timeout=10, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError('Sandy --status falló: ' + result.stderr[:300])
    return json.loads(result.stdout)


def _assert_clean(executable):
    summary = _status(executable).get('summary', {})
    keys = ('stale_instances', 'incomplete_instances', 'stale_retry_containers',
            'incomplete_retry_containers', 'orphaned_tasks')
    dirty = {k: int(summary.get(k, 0) or 0) for k in keys if int(summary.get(k, 0) or 0)}
    if dirty:
        raise RuntimeError('Sandy no está limpio; se abortan pruebas ACL sin ejecutar --cleanup: ' + repr(dirty))


class SandyIntegrationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        context_error = SandySandboxAdapter.execution_context_error()
        if context_error:
            raise RuntimeError(context_error)
        cls.adapter = SandySandboxAdapter()
        if not cls.adapter._available:
            raise RuntimeError('Sandy CLI no disponible')
        _assert_clean(cls.adapter.executable)
        cls.base = Path(tempfile.mkdtemp(prefix='exesoft-sandy-owned-'))
        cls.workspace = cls.base / 'workspace'
        cls.protected = cls.base / 'protected'
        cls.workspace.mkdir()
        cls.protected.mkdir()
        cls.keep_diagnostics = False
        probe = cls.adapter.preflight(cls.workspace, timeout=15)
        if not probe.success:
            cls.keep_diagnostics = True
            print(f'Preflight Sandy falló ({probe.sandy_exit_code})')
            print(f'Stdout:\n{probe.stdout[-2000:]}')
            print(f'Stderr/log:\n{probe.stderr[-4000:]}')
            print(f'Preserving Sandy diagnostics under {cls.base}')
            raise RuntimeError('Falló cmd.exe→Python mínimo; pruebas restantes omitidas')
        try:
            _assert_clean(cls.adapter.executable)
        except Exception:
            cls.keep_diagnostics = True
            print(f'Preserving Sandy diagnostics under {cls.base}')
            raise

    def setUp(self):
        if type(self).keep_diagnostics:
            self.skipTest('Preflight o prueba anterior falló; se preservan recursos y ACL para diagnóstico')
        for child in self.workspace.iterdir():
            if child.name == '.sandy':
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in self.protected.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    def tearDown(self):
        # Preserve runtime/logs whenever a test or cleanup check fails.
        outcome = getattr(self, '_outcome', None)
        result = getattr(outcome, 'result', None)
        test_failed = bool(result and (result.failures or result.errors))
        type(self).keep_diagnostics = type(self).keep_diagnostics or test_failed
        try:
            _assert_clean(self.adapter.executable)
        except Exception:
            type(self).keep_diagnostics = True
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.keep_diagnostics:
            print(f'Preserving Sandy diagnostics under {cls.base}')
            return
        _assert_clean(cls.adapter.executable)
        shutil.rmtree(cls.base)

    def _python(self, code, timeout=15):
        return self.adapter.execute_argv(self.workspace, [sys.executable, '-I', '-c', code], timeout=timeout)

    def test_10_python_and_write_inside_workspace(self):
        result = self._python("from pathlib import Path; Path('ok.txt').write_text('ok'); print('SANDY_OK')")
        self.assertTrue(result.success, result.stderr)
        self.assertEqual((self.workspace / 'ok.txt').read_text(encoding='utf-8'), 'ok')
        self.assertIn('SANDY_OK', result.stdout)

    def test_write_outside_workspace_is_explicitly_denied(self):
        target = self.protected / 'blocked.txt'
        code = "from pathlib import Path; Path(r'" + str(target) + "').write_text('no')"
        result = self._python(code)
        text = (result.stdout + result.stderr).lower()
        self.assertFalse(target.exists(), 'Sandy permitió escribir fuera del workspace')
        self.assertNotEqual(result.returncode, 0, 'La operación no produjo error de acceso')
        self.assertTrue(any(x in text for x in ('permissionerror', 'access is denied', 'permission denied')),
                        'La salida no prueba una denegación ACL: ' + text[-500:])

    def test_network_denial_is_explicit(self):
        code = ("import socket,sys; s=socket.socket(); s.settimeout(2); "
                "\ntry: s.connect(('1.1.1.1',443)); print('CONNECTED'); sys.exit(12)"
                "\nexcept PermissionError as e: print('DENIED',type(e).__name__); sys.exit(13)"
                "\nexcept OSError as e: print('INCONCLUSIVE',type(e).__name__); sys.exit(14)"
                "\nfinally: s.close()")
        result = self._python(code, timeout=8)
        self.assertEqual(result.returncode, 13, result.stdout + result.stderr)
        self.assertIn('DENIED PermissionError', result.stdout)

    def test_sandy_timeout_and_sha256(self):
        result = self._python('import time; time.sleep(8)', timeout=2)
        self.assertEqual(result.sandy_exit_code, 130, result.stderr)
        self.assertEqual(result.error, 'timeout')
        self.assertEqual(len(result.sha256), 64)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SandyIntegrationChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
