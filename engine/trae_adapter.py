"""
TRAE Sandbox Adapter para EXESOFT AI FACTORY.

Backend ligero de aislamiento que usa el ejecutable TRAE Sandbox CLI
existente en LOCALAPPDATA\\Programs\\TRAE\\resources\\app\\modules\\sandbox\\

Sin instalar dependencias. Usa rutas configurables y almacén dentro del workspace.
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from engine.runtime import Result, now
from engine.execution_sandbox import ExecutionDecision, ExecutionDenied


@dataclass
class TraeSandboxResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int
    duration_ms: int
    error: str = ""


class TraeSandboxAdapter:
    """
    Adaptador que ejecuta comandos dentro del sandbox ligero TRAE.

    Características:
    - No requiere privilegios de administrador
    - No abre ventanas de Windows Sandbox
    - Usa almacén temporal dentro del workspace de la Factory
    - Cumple con política fail-closed
    """

    SANDBOX_EXE = Path(
        os.path.expandvars(
            os.environ.get(
                'TRAE_SANDBOX_PATH',
                str(Path(os.environ.get('LOCALAPPDATA', r'%LOCALAPPDATA%')) / 'Programs' / 'TRAE' / 'resources' / 'app' / 'modules' / 'sandbox' / 'trae-sandbox.exe')
            )
        )
    ).resolve()

    DEFAULT_MEMORY_MB = 1024
    DEFAULT_TIMEOUT = 300  # 5 minutes

    def __init__(self, storage_path: Optional[Path] = None, config_name: str = 'exesoft-factory'):
        self.storage_path = storage_path
        self.config_name = config_name
        self._available = self._verify_installation()
        self._config_initialized = False

    def _verify_installation(self) -> bool:
        """Verifica que el ejecutable TRAE Sandbox esté disponible y funcional.

        Realiza environment-check para verificar:
        - SDK loaded successfully
        - Dependencies installed
        - Kernel ready
        """
        try:
            if not self.SANDBOX_EXE.exists():
                return False

            # Verify it's a valid executable
            result = subprocess.run(
                [str(self.SANDBOX_EXE), 'help'],
                capture_output=True, text=True, timeout=10
            )
            if 'trae-sandbox' not in result.stdout.lower() and result.returncode != 0:
                return False

            # Run environment-check to verify SDK is functional
            import tempfile
            tmp = tempfile.mkdtemp()
            storage = Path(tmp) / '.trae-sandbox'
            storage.mkdir(parents=True, exist_ok=True)

            env_result = subprocess.run(
                [str(self.SANDBOX_EXE), 'environment-check',
                 '--storage-path', str(storage),
                 '--config-name', 'exesoft-verify'],
                capture_output=True, text=True, timeout=30
            )

            # Check if environment is supported
            if env_result.returncode != 0:
                return False

            # Parse the output to check for supported: true
            output = env_result.stdout.lower()
            if 'supported: false' in output:
                return False  # SDK not functional (sdk_load_failed, etc.)

            return True
        except Exception:
            return False

    def _ensure_config(self, workspace) -> bool:
        """Genera configuración del sandbox si no existe."""
        if self._config_initialized:
            return True

        workspace = Path(workspace)

        storage = (workspace / '.trae-sandbox').resolve()
        storage.mkdir(parents=True, exist_ok=True)

        try:
            result = subprocess.run(
                [
                    str(self.SANDBOX_EXE), 'upsert-config',
                    '--storage-path', str(storage),
                    '--config-name', self.config_name
                ],
                capture_output=True, text=True, timeout=30
            )
            self._config_initialized = result.returncode == 0
            return self._config_initialized
        except Exception:
            return False

    def check_environment(self, workspace) -> tuple[bool, str]:
        """
        Verifica el entorno de sandbox.

        Returns:
            (success, message)
        """
        if not self._available:
            return False, f'TRAE Sandbox no encontrado en: {self.SANDBOX_EXE}'

        workspace = Path(workspace)

        try:
            result = subprocess.run(
                [
                    str(self.SANDBOX_EXE), 'environment-check',
                    '--storage-path', str((workspace / '.trae-sandbox').resolve()),
                    '--config-name', self.config_name
                ],
                capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0, result.stdout + result.stderr
        except Exception as e:
            return False, str(e)

    def execute(
        self,
        workspace,
        command: str,
        timeout: int = None,
        memory_mb: int = None,
        allowed_paths: list[Path] = None
    ) -> TraeSandboxResult:
        """
        Ejecuta un comando dentro del TRAE Sandbox.

        Args:
            workspace: Directorio del proyecto (almacén del sandbox)
            command: Comando a ejecutar
            timeout: Timeout en segundos
            memory_mb: Límite de memoria
            allowed_paths: Rutas permitidas en el sandbox

        Returns:
            TraeSandboxResult con stdout, stderr, returncode
        """
        workspace = Path(workspace)
        start_time = time.time()
        timeout = timeout or self.DEFAULT_TIMEOUT
        memory_mb = memory_mb or self.DEFAULT_MEMORY_MB

        # Verificar disponibilidad
        if not self._available:
            return TraeSandboxResult(
                success=False, stdout='', stderr='TRAE Sandbox no disponible',
                returncode=-1, duration_ms=0, error='not_available'
            )

        # Asegurar configuración
        if not self._ensure_config(workspace):
            return TraeSandboxResult(
                success=False, stdout='', stderr='Fallo al inicializar configuración',
                returncode=-1, duration_ms=0, error='config_failed'
            )

        storage_path = (workspace / '.trae-sandbox').resolve()

        try:
            result = subprocess.run(
                [
                    str(self.SANDBOX_EXE), 'exec',
                    '--storage-path', str(storage_path),
                    '--config-name', self.config_name,
                    '--shell-path', '/bin/bash',
                    '--command-line', command
                ],
                capture_output=True, text=True,
                timeout=timeout + 10,  # Extra 10s for startup
                cwd=str(workspace)
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return TraeSandboxResult(
                success=result.returncode == 0,
                stdout=result.stdout[-10000:] if result.stdout else '',  # Limit output
                stderr=result.stderr[-10000:] if result.stderr else '',
                returncode=result.returncode,
                duration_ms=duration_ms
            )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return TraeSandboxResult(
                success=False, stdout='', stderr=f'Timeout después de {timeout}s',
                returncode=-1, duration_ms=duration_ms, error='timeout'
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return TraeSandboxResult(
                success=False, stdout='', stderr=str(e),
                returncode=-1, duration_ms=duration_ms, error=type(e).__name__
            )

    def validate_isolation(self, workspace, test_paths: list[Path]) -> list[str]:
        """
        Valida que el aislamiento funciona correctamente.

        Prueba escritura en rutas prohibidas desde dentro del sandbox.
        """
        workspace = Path(workspace)
        violations = []

        # Test 1: Attempt to write outside workspace
        for test_path in test_paths:
            result = self.execute(
                str(workspace),
                f'echo "test" > "{test_path}" 2>/dev/null && test -f "{test_path}"'
            )
            if 'test -f' in result.stdout or result.returncode == 0:
                violations.append(f'Escritura permitida en: {test_path}')

        return violations


class TraeExecutionPolicy:
    """
    Política de ejecución basada en TRAE Sandbox.
    Implementa fail-closed: si el sandbox falla, se niega la ejecución.
    """

    def __init__(self, adapter: TraeSandboxAdapter = None):
        self.adapter = adapter or TraeSandboxAdapter()
        self._available = adapter is not None and adapter._available

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

        if requested_network and not self._available:
            return ExecutionDecision(False, 'Red externa requiere sandbox habilitado')

        if not trusted_code:
            if not self._available:
                return ExecutionDecision(
                    False,
                    'No hay aislamiento de procesos validado; ejecución de código no confiable bloqueada'
                )
            return ExecutionDecision(True, 'Ejecución autorizada en TRAE Sandbox', isolation='trae_sandbox')

        return ExecutionDecision(True, 'Código de confianza autorizado', isolation='trusted')

    def require(self, **kwargs) -> None:
        """Lanza excepción si la operación es denegada."""
        decision = self.evaluate(**kwargs)
        if not decision.allowed:
            raise ExecutionDenied(decision.reason)