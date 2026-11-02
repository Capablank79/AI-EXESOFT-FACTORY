"""Tests for Sandy Sandbox Adapter."""
import unittest
import tempfile
import json
import subprocess
import sys
import tomllib
import zipfile
import io
from pathlib import Path
from unittest.mock import patch, MagicMock

from engine.sandy_adapter import SandySandboxAdapter, SandYSandboxResult, SandYExecutionPolicy
from engine.execution_sandbox import ExecutionDenied


class SandySandboxAdapterTests(unittest.TestCase):
    """Tests for Sandy Sandbox Adapter."""

    def setUp(self):
        context = patch.object(SandySandboxAdapter, 'execution_context_error', return_value='')
        context.start()
        self.addCleanup(context.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)

    def test_adapter_detection(self):
        """Check adapter initialization without launching a local Sandy binary."""
        with patch.object(SandySandboxAdapter, '_verify_installation', return_value=False):
            adapter = SandySandboxAdapter()
        self.assertFalse(adapter._available)

    def test_restricted_context_never_probes_sandy(self):
        exe = self.workspace / 'sandy.exe'
        with patch.object(SandySandboxAdapter, 'execution_context_error', return_value='wrong token'), \
             patch.object(SandySandboxAdapter, '_verify_installation') as verify:
            adapter = SandySandboxAdapter(executable=exe)
        self.assertFalse(adapter._available)
        self.assertEqual(adapter._execution_context_error, 'wrong token')
        verify.assert_not_called()

    def test_generated_config_grants_only_the_sandbox_workspace(self):
        adapter = SandySandboxAdapter.__new__(SandySandboxAdapter)
        adapter.timeout = 120
        adapter.memory_mb = 1024
        adapter.max_processes = 10
        config = adapter._generate_config(self.workspace, 'permissions')
        parsed = tomllib.loads(config.read_text(encoding='utf-8'))
        grants = parsed['allow']['deep']
        self.assertEqual(grants, {'all': [str(self.workspace.resolve())]})

    def test_generated_config_can_explicitly_grant_nested_runtime_only(self):
        adapter = SandySandboxAdapter.__new__(SandySandboxAdapter)
        adapter.timeout = 120
        adapter.memory_mb = 1024
        adapter.max_processes = 10
        runtime = self.workspace / '.sandy' / 'python-3.12.10-embed-amd64'
        runtime.mkdir(parents=True)
        config = adapter._generate_config(self.workspace, 'runtime',
                                          additional_allow_paths=[runtime])
        parsed = tomllib.loads(config.read_text(encoding='utf-8'))
        self.assertEqual(parsed['allow']['deep']['all'],
                         [str(self.workspace.resolve()), str(runtime.resolve())])

    def test_preflight_runs_cmd_before_python_with_separate_argv(self):
        adapter = SandySandboxAdapter.__new__(SandySandboxAdapter)
        adapter.execute_argv = MagicMock(side_effect=[
            SandYSandboxResult(True, '', '', 0, 1),
            SandYSandboxResult(True, '', '', 0, 1),
        ])
        result = adapter.preflight(self.workspace)
        self.assertTrue(result.success)
        first = adapter.execute_argv.call_args_list[0].args[1]
        second = adapter.execute_argv.call_args_list[1].args[1]
        self.assertTrue(first[0].lower().endswith('cmd.exe'))
        self.assertEqual(first[1:], ['/d', '/s', '/c', 'exit 0'])
        self.assertEqual(second, [str(Path(sys.executable).resolve()), '-I', '-c', 'pass'])

    def test_sandy_execution_policy(self):
        """Test execution policy without adapter."""
        policy = SandYExecutionPolicy(adapter=MagicMock(_available=False, allow_network=False))

        # Untrusted code should be denied without sandbox
        decision = policy.evaluate(trusted_code=False)
        self.assertFalse(decision.allowed)
        self.assertIn('Sandy', decision.reason)

        # Trusted code should be allowed
        decision = policy.evaluate(trusted_code=True)
        self.assertFalse(decision.allowed)
        available = MagicMock(_available=True, allow_network=False)
        decision = SandYExecutionPolicy(adapter=available).evaluate(trusted_code=False)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.isolation, 'sandy_sandbox')

    def test_host_paths_denied(self):
        """Test that host paths are always denied."""
        policy = SandYExecutionPolicy(adapter=MagicMock(_available=True, allow_network=False))

        decision = policy.evaluate(trusted_code=True, requested_host_paths=True)
        self.assertFalse(decision.allowed)
        self.assertIn('host', decision.reason.lower())

    def test_network_denied_by_default(self):
        """Test that network is denied by default."""
        policy = SandYExecutionPolicy(adapter=MagicMock(_available=True, allow_network=False))

        decision = policy.evaluate(trusted_code=False, requested_network=True)
        self.assertFalse(decision.allowed)

    def test_isolation_verification(self):
        """Verify argv handoff and evidence without launching Sandy or changing ACL."""
        exe = self.workspace / 'sandy.exe'
        status = {'summary': {'stale_instances': 0, 'incomplete_instances': 0,
                              'stale_retry_containers': 0, 'incomplete_retry_containers': 0,
                              'orphaned_tasks': 0}, 'instances': [], 'retry_containers': []}
        version = MagicMock(returncode=0, stdout='sandy v0.9996', stderr='')
        status_result = MagicMock(returncode=0, stdout=json.dumps(status), stderr='')
        runtime = self.workspace / '.sandy' / 'python-3.12.10-embed-amd64'
        runtime.mkdir(parents=True)
        with patch('engine.sandy_adapter.subprocess.run',
                   side_effect=[version, status_result, status_result]) as run, \
             patch.object(SandySandboxAdapter, '_run_sandy_and_wait_for_restore',
                          return_value=(subprocess.CompletedProcess([], 0, 'ok', ''), False)) as launch_run, \
             patch.object(SandySandboxAdapter, '_prepare_minimal_python',
                          return_value=runtime / 'python.exe'):
            adapter = SandySandboxAdapter(executable=exe)
            result = adapter.execute_argv(self.workspace,
                                          [str(Path(sys.executable).resolve()), '-I', '-c', 'print(1)'])
        self.assertTrue(result.success)
        self.assertEqual(result.stdout, 'ok')
        invocation = launch_run.call_args.args[0]
        self.assertIn('-x', invocation)
        self.assertEqual(invocation[invocation.index('-x') + 1:],
                         [str(runtime / 'python.exe'), '-I', '-S', '-B', '-c', 'print(1)'])
        config_path = Path(invocation[invocation.index('-c') + 1])
        self.assertEqual(tomllib.loads(config_path.read_text(encoding='utf-8'))['allow']['deep']['all'],
                         [str(self.workspace.resolve()), str(runtime.resolve())])
        self.assertEqual(len(result.sha256), 64)

    def test_embedded_archive_requires_complete_isolated_layout(self):
        names = ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                 'python312.zip', 'python312._pth', 'vcruntime140.dll')
        stdlib_bytes = io.BytesIO()
        with zipfile.ZipFile(stdlib_bytes, 'w') as stdlib:
            stdlib.writestr('encodings/__init__.pyc', b'fixture')
            stdlib.writestr('site-packages/README.txt',
                            b'This directory exists so that 3rd party packages can be installed\r\n'
                            b'here.  Read the source for site.py for more details.\r\n')
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            for name in names:
                data = (b'python312.zip\n.\n#import site\n' if name == 'python312._pth'
                        else stdlib_bytes.getvalue() if name == 'python312.zip' else b'fixture')
                archive.writestr(name, data)
        raw.seek(0)
        with zipfile.ZipFile(raw) as archive:
            normalized = SandySandboxAdapter._validate_embedded_archive(archive)
        self.assertEqual(set(normalized), set(names))

    def test_embedded_archive_rejects_global_search_path(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                         'python312.zip', 'python312._pth', 'vcruntime140.dll'):
                content = b'python312.zip\nC:\\Python312\\Lib\n.\n' if name == 'python312._pth' else b'fixture'
                archive.writestr(name, content)
        raw.seek(0)
        with zipfile.ZipFile(raw) as archive:
            with self.assertRaisesRegex(RuntimeError, 'python312._pth'):
                SandySandboxAdapter._validate_embedded_archive(archive)

    def test_embedded_archive_rejects_real_site_packages_contents(self):
        stdlib_bytes = io.BytesIO()
        with zipfile.ZipFile(stdlib_bytes, 'w') as stdlib:
            stdlib.writestr('encodings/__init__.pyc', b'fixture')
            stdlib.writestr('site-packages/README.txt',
                            b'This directory exists so that 3rd party packages can be installed\r\n'
                            b'here.  Read the source for site.py for more details.\r\n')
            stdlib.writestr('site-packages/untrusted_module.pyc', b'not allowed')
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                         'python312.zip', 'python312._pth', 'vcruntime140.dll'):
                content = (b'python312.zip\n.\n#import site\n' if name == 'python312._pth'
                           else stdlib_bytes.getvalue() if name == 'python312.zip' else b'fixture')
                archive.writestr(name, content)
        raw.seek(0)
        with zipfile.ZipFile(raw) as archive:
            with self.assertRaisesRegex(RuntimeError, 'biblioteca estándar aislada'):
                SandySandboxAdapter._validate_embedded_archive(archive)

    def test_embedded_pe_architecture_is_checked_without_execution(self):
        image = bytearray(0x80)
        image[:2] = b'MZ'
        image[0x3C:0x40] = (0x40).to_bytes(4, 'little')
        image[0x40:0x44] = b'PE\0\0'
        image[0x44:0x46] = (0x8664).to_bytes(2, 'little')
        self.assertEqual(SandySandboxAdapter._pe_machine_from_bytes(bytes(image)), 0x8664)
        image[0x44:0x46] = (0x14C).to_bytes(2, 'little')
        self.assertNotEqual(SandySandboxAdapter._pe_machine_from_bytes(bytes(image)), 0x8664)

    def test_official_embedded_archive_and_preparer_without_sandy(self):
        package = Path(__file__).resolve().parent.parent / 'tools' / SandySandboxAdapter.EMBEDDED_PYTHON
        self.assertTrue(package.is_file(), 'Falta el ZIP oficial CPython 3.12.10 x64')
        checksum_before = SandySandboxAdapter._file_sha256(package)
        with zipfile.ZipFile(package) as archive:
            SandySandboxAdapter._validate_embedded_archive(archive)
        adapter = SandySandboxAdapter.__new__(SandySandboxAdapter)
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            staged = adapter._prepare_minimal_python(workspace)
            self.assertTrue(staged.is_file())
            self.assertTrue(staged.is_relative_to(workspace.resolve()))
            self.assertEqual(staged.parent.name, 'python-3.12.10-embed-amd64')
        self.assertEqual(SandySandboxAdapter._file_sha256(package), checksum_before)

    def test_string_command_does_not_nest_cmd_shells(self):
        exe = self.workspace / 'sandy.exe'
        status = {'summary': {'stale_instances': 0, 'incomplete_instances': 0,
                              'stale_retry_containers': 0, 'incomplete_retry_containers': 0,
                              'orphaned_tasks': 0}, 'instances': [], 'retry_containers': []}
        version = MagicMock(returncode=0, stdout='sandy v0.9996', stderr='')
        status_result = MagicMock(returncode=0, stdout=json.dumps(status), stderr='')
        with patch('engine.sandy_adapter.subprocess.run',
                   side_effect=[version, status_result, status_result]) as run, \
             patch.object(SandySandboxAdapter, '_run_sandy_and_wait_for_restore',
                          return_value=(subprocess.CompletedProcess([], 0, 'ok', ''), False)) as launch_run:
            adapter = SandySandboxAdapter(executable=exe)
            result = adapter.execute(self.workspace,'cmd /c "echo quoted > output.txt"')
        self.assertTrue(result.success)
        invocation = launch_run.call_args.args[0]
        args = invocation[invocation.index('-x') + 1:]
        self.assertTrue(args[0].lower().endswith('cmd.exe'))
        self.assertEqual(args[1:4], ['/d','/s','/c'])
        self.assertFalse(args[4].lower().startswith('cmd'))
        self.assertEqual(sum(arg.lower().endswith('cmd.exe') for arg in args), 1)

    def test_timeout_expired_keeps_partial_output_and_hash(self):
        exe=self.workspace/'sandy.exe'
        status={'summary':{'stale_instances':0,'incomplete_instances':0,
                           'stale_retry_containers':0,'incomplete_retry_containers':0,
                           'orphaned_tasks':0},'instances':[],'retry_containers':[]}
        version=MagicMock(returncode=0,stdout='sandy v0.9996',stderr='')
        status_result=MagicMock(returncode=0,stdout=json.dumps(status),stderr='')
        expired=subprocess.CompletedProcess([], 130, 'partial-out', 'partial-err')
        with patch('engine.sandy_adapter.subprocess.run',side_effect=[version,status_result,status_result]), \
             patch.object(SandySandboxAdapter, '_run_sandy_and_wait_for_restore',
                          return_value=(expired, True)):
            result=SandySandboxAdapter(executable=exe).execute_argv(self.workspace,['python.exe','-c','pass'])
        self.assertEqual(result.error,'timeout')
        self.assertIn('partial-out',result.stdout)
        self.assertIn('partial-err',result.stderr)
        self.assertEqual(len(result.sha256),64)

    def test_sandy_exit_130_is_a_real_timeout(self):
        exe=self.workspace/'sandy.exe'
        status={'summary':{'stale_instances':0,'incomplete_instances':0,
                           'stale_retry_containers':0,'incomplete_retry_containers':0,
                           'orphaned_tasks':0},'instances':[],'retry_containers':[]}
        version=MagicMock(returncode=0,stdout='sandy v0.9996',stderr='')
        status_result=MagicMock(returncode=0,stdout=json.dumps(status),stderr='')
        timed_out=subprocess.CompletedProcess([], 130, 'timeout output', '')
        with patch('engine.sandy_adapter.subprocess.run',side_effect=[version,status_result,status_result]), \
             patch.object(SandySandboxAdapter, '_run_sandy_and_wait_for_restore',
                          return_value=(timed_out, False)):
            result=SandySandboxAdapter(executable=exe).execute_argv(self.workspace,['python.exe','-c','pass'],timeout=2)
        self.assertFalse(result.success)
        self.assertEqual(result.error,'timeout')
        self.assertEqual(result.sandy_exit_code,130)

    def test_stale_cleanup_refuses_foreign_resources(self):
        adapter = SandySandboxAdapter.__new__(SandySandboxAdapter)
        adapter.executable = self.workspace / 'sandy.exe'
        adapter._status = lambda: {'summary': {'stale_instances':1},
                                   'instances':[{'status':'stale','config_source':str(self.workspace.parent/'other'/'sandy.toml')}],
                                   'retry_containers':[]}
        with patch('engine.sandy_adapter.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError,'no se ejecutó --cleanup'):
                adapter._recover_owned_stale(self.workspace)
        run.assert_not_called()

    def test_stale_cleanup_uses_official_command_for_owned_workspace(self):
        adapter = SandySandboxAdapter.__new__(SandySandboxAdapter)
        adapter.executable = self.workspace / 'sandy.exe'
        adapter.STATUS_TIMEOUT = 10
        stale = {'summary': {'stale_instances':1},
                 'instances':[{'status':'stale','config_source':str(self.workspace/'.sandy'/'exesoft-run.toml')}],
                 'retry_containers':[]}
        clean = {'summary': {'stale_instances':0,'incomplete_instances':0,
                             'stale_retry_containers':0,'incomplete_retry_containers':0,
                             'orphaned_tasks':0}, 'instances':[],'retry_containers':[]}
        adapter._status = MagicMock(side_effect=[stale,clean])
        with patch.object(adapter, '_run_sandy_and_wait_for_restore',
                          return_value=(subprocess.CompletedProcess([], 0, '', ''), False)) as launch:
            adapter._recover_owned_stale(self.workspace)
        self.assertEqual(launch.call_args.args[0],[str(adapter.executable),'--cleanup'])


if __name__ == '__main__':
    unittest.main()
