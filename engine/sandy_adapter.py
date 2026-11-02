"""
Sandy Sandbox Adapter para EXESOFT AI FACTORY v0.9996.

Backend de ejecución aislada mediante Sandy CLI v0.9996.
La disponibilidad real de AppContainer se comprueba al ejecutar; detectar --version
no acredita que el sistema permita crear ACLs temporales.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import sys
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from engine.execution_sandbox import ExecutionDecision, ExecutionDenied


@dataclass(frozen=True)
class SandYSandboxResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int
    duration_ms: int
    sha256: str = ""
    error: str = ""
    sandy_exit_code: int = -1  # 125-131 = Sandy executor errors
    effective_argv: tuple[str, ...] = ()


class SandySandboxAdapter:
    """
    Adaptador que ejecuta comandos dentro de Sandy AppContainer.

    Características:
    - Sin ventanas virtuales (AppContainer)
    - Sin Docker/WSL2/Hyper-V
    - Escritura controlada por permisos TOML
    - Red bloqueada por defecto
    - Timeout configurable
    """

    DEFAULT_MEMORY_MB = 1024
    DEFAULT_TIMEOUT = 120
    DEFAULT_MAX_PROCESSES = 10
    OUTER_TIMEOUT_GRACE = 30
    STATUS_TIMEOUT = 10
    EMBEDDED_PYTHON = 'python-3.12.10-embed-amd64.zip'
    EMBEDDED_PYTHON_MD5 = 'fe8ef205f2e9c3ba44d0cf9954e1abd3'

    def __init__(
        self,
        executable: Optional[Path] = None,
        memory_mb: int = None,
        timeout: int = None,
        max_processes: int = None,
        allow_network: bool = False
    ):
        self.memory_mb = self._positive_int(memory_mb, self.DEFAULT_MEMORY_MB, 'memory_mb')
        self.timeout = self._positive_int(timeout, self.DEFAULT_TIMEOUT, 'timeout')
        self.max_processes = self._positive_int(max_processes, self.DEFAULT_MAX_PROCESSES, 'max_processes')
        if allow_network:
            raise ValueError("Este adaptador solo admite red bloqueada")
        self.allow_network = False
        self.version = ''
        self._execution_context_error = self.execution_context_error()
        # Do not even probe Sandy from a restricted/non-host token.
        self.executable = Path(executable) if executable else None
        self._available = False
        if not self._execution_context_error:
            self.executable = self.executable or self._find_executable()
            self._available = self._verify_installation()

    @staticmethod
    def execution_context_error() -> str:
        """Prevent a Codex restricted token from starting Sandy under another user's HKCU."""
        if os.name != 'nt':
            return 'Sandy CLI solo puede ejecutarse desde Windows'
        profile = os.environ.get('USERPROFILE', '').rstrip('\\/')
        expected = Path(profile).name if profile else ''
        whoami = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'whoami.exe'
        if not expected or not whoami.is_file():
            return 'No se pudo verificar la identidad anfitriona contra USERPROFILE'
        try:
            result = subprocess.run([str(whoami)], capture_output=True, text=True,
                                    timeout=3, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 'No se pudo consultar la identidad efectiva: ' + type(exc).__name__
        actual = result.stdout.strip().splitlines()[-1].rsplit('\\', 1)[-1] if result.returncode == 0 and result.stdout.strip() else ''
        if not actual or actual.casefold() != expected.casefold():
            return f'Contexto de ejecución no anfitrión: token={actual or "desconocido"}, perfil={expected}'
        return ''

    @staticmethod
    def _positive_int(value, default, label):
        if value is None:
            return default
        value = int(value)
        if value < 1:
            raise ValueError(f'{label} debe ser positivo')
        return value

    @staticmethod
    def _text(value):
        if isinstance(value, bytes):
            return value.decode('utf-8', errors='replace')
        return value or ''

    def _find_executable(self) -> Path:
        """Localiza Sandy instalado por el operador."""
        candidates = []
        if os.environ.get('SANDY_EXE'):
            candidates.append(Path(os.environ['SANDY_EXE']))
        if os.environ.get('USERPROFILE'):
            candidates.append(Path(os.environ['USERPROFILE']) / 'Sandy-Evaluation' / 'sandy.exe')
        candidates.append(Path.home() / 'Sandy-Evaluation' / 'sandy.exe')
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError('Sandy CLI no encontrado; configure SANDY_EXE')

    def _verify_installation(self) -> bool:
        try:
            result = subprocess.run([str(self.executable), '--version'],
                                    capture_output=True, text=True, timeout=self.STATUS_TIMEOUT,
                                    stdin=subprocess.DEVNULL)
            output = (result.stdout or '') + ' ' + (result.stderr or '')
            match = re.search(r'\bv?(\d+\.\d+(?:\.\d+)?)\b', output, flags=re.IGNORECASE)
            self.version = match.group(1) if match else ''
            return result.returncode == 0 and 'sandy' in output.lower() and self.version == '0.9996'
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _status(self):
        result = subprocess.run([str(self.executable), '--status', '--json'],
                                capture_output=True, text=True, timeout=self.STATUS_TIMEOUT,
                                stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError('Sandy --status falló: ' + self._text(result.stderr)[:300])
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or not isinstance(data.get('summary'), dict):
            raise RuntimeError('Sandy --status devolvió un formato desconocido')
        return data

    @staticmethod
    def _run_sandy_and_wait_for_restore(argv, *, timeout, cwd=None):
        """Let Sandy finish ACL rollback even when it outlives the outer grace."""
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, errors='replace', cwd=cwd,
                                   stdin=subprocess.DEVNULL)
        exceeded_grace = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            exceeded_grace = True
            # Popen.communicate(timeout=...) leaves Sandy alive. Wait for its
            # own watchdog and ACL restoration; never kill it from the wrapper.
            stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), exceeded_grace

    @staticmethod
    def _stale_counts(status):
        summary = status.get('summary', {})
        keys = ('stale_instances', 'incomplete_instances', 'stale_retry_containers',
                'incomplete_retry_containers', 'orphaned_tasks')
        return {key: int(summary.get(key, 0) or 0) for key in keys}

    @staticmethod
    def _entry_is_stale(entry):
        if entry.get('stale') is True or entry.get('incomplete') is True:
            return True
        labels = (entry.get('state'), entry.get('status'), entry.get('recovery_state'))
        return any(isinstance(x, str) and x.lower() in
                   {'stale', 'incomplete', 'recovery', 'retry', 'orphaned'} for x in labels)

    @staticmethod
    def _entry_belongs_to_workspace(entry, workspace):
        keys = ('config', 'config_path', 'config_source', 'workdir', 'working_directory',
                'workspace', 'workspace_path', 'cwd', 'log', 'logfile', 'path')
        for key in keys:
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                continue
            try:
                path = Path(value).resolve()
            except (OSError, ValueError):
                continue
            if path == workspace or workspace in path.parents:
                return True
        return False

    def _recover_owned_stale(self, workspace):
        """Run official global cleanup only when every stale record is proven ours."""
        status = self._status()
        counts = self._stale_counts(status)
        if not any(counts.values()):
            return
        if counts['orphaned_tasks']:
            raise RuntimeError('Sandy reporta tareas huérfanas sin propiedad comprobable')
        stale = []
        for key in ('instances', 'retry_containers'):
            values = status.get(key, [])
            if not isinstance(values, list):
                raise RuntimeError(f'Formato Sandy desconocido en {key}')
            stale.extend(x for x in values if isinstance(x, dict) and self._entry_is_stale(x))
        expected = sum(counts[k] for k in ('stale_instances', 'incomplete_instances',
                                             'stale_retry_containers', 'incomplete_retry_containers'))
        owned = [x for x in stale if self._entry_belongs_to_workspace(x, workspace)]
        if expected == 0 or len(stale) < expected or len(owned) != expected or len(owned) != len(stale):
            raise RuntimeError('Estado STALE no atribuible solo a este workspace; no se ejecutó --cleanup')
        cleaned, _ = self._run_sandy_and_wait_for_restore(
            [str(self.executable), '--cleanup'], timeout=self.STATUS_TIMEOUT + self.OUTER_TIMEOUT_GRACE)
        if cleaned.returncode:
            raise RuntimeError('Sandy --cleanup falló: ' + self._text(cleaned.stderr)[:300])
        if any(self._stale_counts(self._status()).values()):
            raise RuntimeError('Sandy --cleanup terminó, pero persiste estado STALE')

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())

    @staticmethod
    def _validate_embedded_archive(archive: zipfile.ZipFile) -> list[str]:
        names = archive.namelist()
        normalized = [name.replace('\\', '/') for name in names]
        if len(set(normalized)) != len(normalized) or any(
                name.startswith('/') or '..' in Path(name).parts or ':' in name
                for name in normalized):
            raise RuntimeError('ZIP embebido contiene rutas duplicadas o fuera del paquete')
        files = {name for name in normalized if not name.endswith('/')}
        required = {'python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll',
                    'python312.zip', 'python312._pth', 'vcruntime140.dll'}
        if not required.issubset(files):
            raise RuntimeError('Distribución embebida incompleta: faltan archivos esenciales')
        if any('site-packages' in name.casefold() for name in files):
            raise RuntimeError('La distribución embebida no debe incluir site-packages')
        pth = archive.read('python312._pth').decode('ascii').splitlines()
        paths = [line.strip() for line in pth if line.strip() and not line.lstrip().startswith('#')
                 and line.strip() != 'import site']
        if set(paths) != {'python312.zip', '.'} or any(
                Path(item).is_absolute() or '..' in Path(item).parts for item in paths):
            raise RuntimeError('python312._pth no limita la búsqueda a rutas locales esperadas')
        with zipfile.ZipFile(archive.open('python312.zip')) as stdlib:
            stdlib_names = stdlib.namelist()
            site_entries = {name for name in stdlib_names
                            if any(part.casefold() == 'site-packages'
                                   for part in Path(name).parts)}
            official_marker = 'site-packages/README.txt'
            marker_text = (b'This directory exists so that 3rd party packages can be installed\r\n'
                           b'here.  Read the source for site.py for more details.\r\n')
            invalid_site_entries = (site_entries != {official_marker}
                                    or stdlib.read(official_marker) != marker_text) if site_entries else False
            if ('encodings/__init__.pyc' not in stdlib_names or invalid_site_entries or any(
                    '..' in Path(name).parts for name in stdlib_names)):
                raise RuntimeError('python312.zip no contiene una biblioteca estándar aislada')
        return normalized

    def _prepare_minimal_python(self, workspace: Path) -> Path:
        """Extract only the pinned official CPython 3.12.10 x64 embeddable package."""
        project = Path(__file__).resolve().parent.parent
        package = project / 'tools' / self.EMBEDDED_PYTHON
        if not package.is_file():
            raise RuntimeError(f'Falta el paquete oficial embebible verificado: {package}')
        # Python.org publishes this release's checksum in its release manifest.
        import hashlib as _hashlib
        md5 = _hashlib.md5(usedforsecurity=False)
        with package.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                md5.update(chunk)
        if md5.hexdigest() != self.EMBEDDED_PYTHON_MD5:
            raise RuntimeError('Checksum oficial del paquete embebible no coincide')

        with zipfile.ZipFile(package) as archive:
            members = self._validate_embedded_archive(archive)
            for name in members:
                if Path(name).suffix.casefold() in {'.exe', '.dll', '.pyd'}:
                    data = archive.read(name)
                    if self._pe_machine_from_bytes(data) != 0x8664:
                        raise RuntimeError(f'Arquitectura distinta de x64: {name}')

        sandy_dir = workspace / '.sandy'
        if sandy_dir.exists() and self._is_link_or_junction(sandy_dir):
            raise RuntimeError('No se permite .sandy como enlace')
        sandy_dir.mkdir(exist_ok=True)
        if sandy_dir.resolve().parent != workspace.resolve():
            raise RuntimeError('Directorio Sandy fuera del workspace')
        runtime = sandy_dir / 'python-3.12.10-embed-amd64'
        if runtime.exists():
            if self._is_link_or_junction(runtime):
                raise RuntimeError('Runtime embebible no confiable')
            # Revalidate every staged file against the verified official archive.
            entries = list(runtime.rglob('*'))
            if any(self._is_link_or_junction(item) for item in entries):
                raise RuntimeError('Runtime embebible contiene enlaces no confiables')
            with zipfile.ZipFile(package) as archive:
                actual = {p.relative_to(runtime).as_posix() for p in runtime.rglob('*') if p.is_file()}
                if actual != {name for name in members if not name.endswith('/')}:
                    raise RuntimeError('Runtime embebible incompleto o con archivos extra')
                if any(p.is_dir() for p in entries):
                    raise RuntimeError('Runtime embebible contiene directorios inesperados')
                for name in actual:
                    if self._file_sha256(runtime / name) != hashlib.sha256(archive.read(name)).hexdigest():
                        raise RuntimeError('Runtime embebible alterado: ' + name)
            return runtime / 'python.exe'
        build = Path(tempfile.mkdtemp(prefix='.python-build-', dir=sandy_dir))
        try:
            with zipfile.ZipFile(package) as archive:
                for name in members:
                    if name.endswith('/'):
                        continue
                    target = build / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
            os.rename(build, runtime)
        except Exception:
            if build.exists() and build.parent == sandy_dir:
                shutil.rmtree(build, ignore_errors=True)
            raise
        return runtime / 'python.exe'

    @staticmethod
    def _pe_machine_from_bytes(data: bytes) -> int:
        if len(data) < 0x40 or data[:2] != b'MZ':
            raise RuntimeError('Archivo PE inválido en ZIP embebible')
        offset = int.from_bytes(data[0x3C:0x40], 'little')
        if offset + 6 > len(data) or data[offset:offset + 4] != b'PE\0\0':
            raise RuntimeError('Cabecera PE inválida en ZIP embebible')
        return int.from_bytes(data[offset + 4:offset + 6], 'little')

    def _generate_config(self, workspace: Path, config_name: str,
                         timeout: Optional[int] = None,
                         memory_mb: Optional[int] = None,
                         additional_allow_paths: Sequence[Path] = ()) -> Path:
        """TOML oficial Sandy v0.9996, UTF-8 sin BOM."""
        workspace = workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise NotADirectoryError(str(workspace))
        config_dir = workspace / '.sandy'
        if config_dir.exists() and self._is_link_or_junction(config_dir):
            raise RuntimeError('No se permite .sandy como enlace')
        config_dir.mkdir(exist_ok=True)
        if config_dir.resolve().parent != workspace:
            raise RuntimeError('Directorio de configuración Sandy fuera del workspace')
        if any(config_dir.glob('.python-build-*')):
            raise RuntimeError('Runtime Python parcial; se aborta antes de conceder ACL')
        allowed_paths = [workspace]
        for raw_path in additional_allow_paths:
            path = Path(raw_path)
            if self._is_link_or_junction(path):
                raise RuntimeError('No se conceden ACL a una ruta de runtime enlazada')
            resolved = path.resolve(strict=True)
            if (not resolved.is_dir() or resolved == workspace or workspace not in resolved.parents):
                raise RuntimeError('Ruta adicional Sandy debe ser un directorio propio del workspace')
            allowed_paths.append(resolved)
        config_path = config_dir / f'{config_name}.toml'
        if config_path.exists() and self._is_link_or_junction(config_path):
            raise RuntimeError('No se permite el TOML Sandy como enlace')
        # No se conceden ACL a Python global. El intérprete mínimo se prepara
        # dentro del workspace antes de Sandy y hereda únicamente este grant.
        def literal(path):
            value = str(path)
            if "'" in value or '\n' in value or '\r' in value:
                raise ValueError('Ruta no representable como literal TOML')
            return "'" + value + "'"
        content = ("[sandbox]\n"
                   "token = 'appcontainer'\n"
                   f"workdir = {literal(workspace)}\n\n"
                   "[allow.deep]\n"
                   f"all = [{', '.join(literal(path) for path in allowed_paths)}]\n\n"
                   "[environment]\n"
                   "inherit = false\n\n"
                   "[limit]\n"
                   f"timeout = {int(timeout if timeout is not None else self.timeout)}\n"
                   f"memory = {int(memory_mb if memory_mb is not None else self.memory_mb)}\n"
                   f"processes = {int(self.max_processes)}\n")
        config_path.write_text(content, encoding='utf-8')
        return config_path

    def preflight(self, workspace: Path, timeout: int = 15) -> SandYSandboxResult:
        """Diagnose process creation with a known-good Windows executable first."""
        workspace = Path(workspace).resolve(strict=True)
        sandy_dir = workspace / '.sandy'
        if sandy_dir.exists() and self._is_link_or_junction(sandy_dir):
            return SandYSandboxResult(False, '', 'Directorio .sandy no confiable', -1, 0,
                                      error='runtime_path_invalid')
        if sandy_dir.exists() and any(sandy_dir.glob('.python-build-*')):
            return SandYSandboxResult(False, '', 'Runtime Python parcial; no se inicia Sandy', -1, 0,
                                      error='runtime_incomplete')
        cmd = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cmd.exe'
        if not cmd.is_file():
            return SandYSandboxResult(False, '', f'cmd.exe no encontrado: {cmd}', -1, 0,
                                      error='cmd_missing')
        smoke = self.execute_argv(workspace, [str(cmd), '/d', '/s', '/c', 'exit 0'], timeout)
        if not smoke.success:
            return smoke
        return self.execute_argv(workspace, [str(Path(sys.executable).resolve()), '-I', '-c', 'pass'], timeout)

    @staticmethod
    def _is_factory_python(path: str) -> bool:
        try:
            candidate = Path(path).resolve()
            allowed = {Path(sys.executable).resolve()}
            base_executable = getattr(sys, '_base_executable', '')
            if base_executable:
                allowed.add(Path(base_executable).resolve())
            return candidate in allowed
        except (OSError, ValueError):
            return False

    def execute_argv(self, workspace: Path, argv: Sequence[str], timeout: Optional[int] = None,
                     memory_mb: Optional[int] = None) -> SandYSandboxResult:
        """Run a program directly in AppContainer; argv avoids Windows shell quoting."""
        workspace = Path(workspace)
        timeout = self.timeout if timeout is None else self._positive_int(timeout, self.timeout, 'timeout')
        memory_mb = self.memory_mb if memory_mb is None else self._positive_int(memory_mb, self.memory_mb, 'memory_mb')
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(x, str) or '\x00' in x for x in argv):
            return SandYSandboxResult(False, '', 'argv inválido', -1, 0, error='invalid_command')
        context_error = self._execution_context_error or self.execution_context_error()
        if context_error:
            return SandYSandboxResult(False, '', context_error, -1, 0, error='wrong_execution_context')
        if not self._available:
            return SandYSandboxResult(False, '', 'Sandy Sandbox no disponible', -1, 0, error='not_available')
        started = time.monotonic()
        effective_argv = list(argv)
        additional_allow_paths = []
        try:
            workspace = workspace.resolve(strict=True)
            if not workspace.is_dir():
                raise NotADirectoryError(str(workspace))
            self._recover_owned_stale(workspace)
            if self._is_factory_python(effective_argv[0]):
                runtime_python = self._prepare_minimal_python(workspace)
                additional_allow_paths.append(runtime_python.parent)
                args = [arg for arg in effective_argv[1:] if arg not in {'-I', '-S', '-B'}]
                effective_argv = [str(runtime_python), '-I', '-S', '-B', *args]
            config_path = self._generate_config(workspace, 'exesoft-run', timeout, memory_mb,
                                                additional_allow_paths=additional_allow_paths)
            log_path = config_path.parent / f'run-{time.time_ns()}.log'
        except Exception as exc:
            return SandYSandboxResult(False, '', str(exc), -1, 0, error='recovery_or_config_failed')

        sandy_cmd = [str(self.executable), '-c', str(config_path), '-l', str(log_path), '-x', *effective_argv]
        try:
            result, exceeded_grace = self._run_sandy_and_wait_for_restore(
                sandy_cmd, timeout=timeout + self.OUTER_TIMEOUT_GRACE, cwd=str(workspace))
            stdout, stderr, code = result.stdout or '', result.stderr or '', result.returncode
            if exceeded_grace:
                stderr += '\nSandy superó el margen externo; se esperó su restauración ACL sin terminarlo.'
            if code and log_path.is_file():
                log_text = log_path.read_text(encoding='utf-8', errors='replace')[-20000:]
                if log_text and log_text not in stderr:
                    stderr = (stderr + '\n--- Sandy log ---\n' + log_text).strip()
                stderr = (stderr + f'\nSandy log path: {log_path}').strip()
            executor_error = 125 <= code <= 131
            error = 'timeout' if code == 130 else ('executor_error' if executor_error else '')
            try:
                self._recover_owned_stale(workspace)
            except Exception as exc:
                stderr += '\nSandy recovery check failed: ' + str(exc)
                error, executor_error = 'recovery_failed', True
            output = stdout + stderr
            return SandYSandboxResult(code == 0 and not executor_error, stdout[-20000:], stderr[-20000:],
                                      code, int((time.monotonic()-started)*1000),
                                      hashlib.sha256(output.encode('utf-8', errors='replace')).hexdigest(),
                                      error, code, tuple(effective_argv))
        except Exception as exc:
            return SandYSandboxResult(False, '', str(exc), -1,
                                      int((time.monotonic()-started)*1000), error=type(exc).__name__)

    def execute(self, workspace: Path, command, timeout: Optional[int] = None,
                memory_mb: Optional[int] = None) -> SandYSandboxResult:
        """Compatibility entry point. Prefer argv; a string runs inside one cmd.exe."""
        if isinstance(command, str):
            command = command.strip()
            command = re.sub(r'^cmd(?:\.exe)?\s+(?:(?:/d)\s+)?/c\s+', '', command,
                             flags=re.IGNORECASE)
            if not command:
                return SandYSandboxResult(False, '', 'Comando vacío', -1, 0, error='invalid_command')
            shell_exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'cmd.exe')
            argv = [shell_exe, '/d', '/s', '/c', command]
        else:
            argv = command
        return self.execute_argv(workspace, argv, timeout, memory_mb)


class SandYExecutionPolicy(ExecutionDecision):
    """
    Política de ejecución basada en Sandy Sandbox.
    Fail-closed: si Sandy falla, se niega la ejecución.
    """

    def __init__(self, adapter: SandySandboxAdapter = None):
        self.adapter = adapter or SandySandboxAdapter()
        self._available = self.adapter._available

    @property
    def available(self) -> bool:
        return self._available

    def evaluate(
        self,
        *,
        trusted_code: bool = False,
        sandbox_attested: bool = False,
        requested_network: bool = False,
        requested_host_paths: bool = False
    ) -> ExecutionDecision:
        """Evalúa si una operación puede ejecutarse."""
        if requested_host_paths:
            return ExecutionDecision(False, 'Acceso a rutas del host prohibido')

        if requested_network and not self.allow_network:
            return ExecutionDecision(False, 'Red externa prohibida por política sandbox')

        if not self._available:
            return ExecutionDecision(False, 'Sandy no está disponible; ejecución denegada')
        return ExecutionDecision(True, 'Ejecución aislada en Sandy AppContainer', isolation='sandy_sandbox')

    @property
    def allow_network(self) -> bool:
        return getattr(self.adapter, 'allow_network', False)

    def require(self, **kwargs) -> None:
        """Lanza excepción si la operación es denegada."""
        decision = self.evaluate(**kwargs)
        if not decision.allowed:
            raise ExecutionDenied(decision.reason)
