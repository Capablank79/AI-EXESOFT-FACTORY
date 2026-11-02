"""
Windows Sandbox Adapter para EXESOFT AI FACTORY.

Requisitos:
- Windows 10/11 Pro/Enterprise
- Feature "Containers-DisposableClientVM" habilitada
- WSB (Windows Sandbox) configurado

Este adaptador implementa aislamiento real de procesos, memoria, red y sistema de archivos
mediante Windows Sandbox, cumpliendo con la política fail-closed de ExecutionSandbox.
"""

from __future__ import annotations
import os
import tempfile
import subprocess
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import json


@dataclass(frozen=True)
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    returncode: int
    error: str = ""


class WindowsSandboxAdapter:
    """
    Adaptador que ejecuta comandos dentro de Windows Sandbox (.wsb).

    Aísla:
    - Procesos (PID namespace separado)
    - Memoria (límites configurables, mín 2048 MB)
    - Red (deshabilitada por defecto; Networking=Disable)
    - Sistema de archivos (solo carpetas mapeadas explícitamente, ReadOnly opcional)
    - Credenciales (no acceso al host; ProtectedClient=Enable)
    - Entrada audio/vídeo/impresora/portapapeles deshabilitada
    """

    def __init__(self, memory_mb: int = 2048, network: bool = False):
        self.memory_mb = max(2048, memory_mb)  # Mínimo 2048 MB según docs
        self.network = network
        self._check_sandbox_available()

    def _check_sandbox_available(self) -> None:
        """Verifica que Windows Sandbox esté disponible.

        Corrección v0.1: Usa métodos que no requieren privilegios de administrador.
        La ubicación del ejecutable WindowsSandbox.exe en System32 indica que la
        característica está instalada, sin necesidad de DISM o PowerShell con admin.
        """
        try:
            result = subprocess.run(
                ["wmic", "os", "get", "Caption"],
                capture_output=True, text=True, timeout=10
            )
            if "Windows 10" not in result.stdout and "Windows 11" not in result.stdout:
                raise RuntimeError("Windows Sandbox requiere Windows 10/11 Pro o Enterprise")

            # Verificar si el ejecutable WindowsSandbox.exe está disponible
            # Esto no requiere privilegios de administrador
            exe_path = Path("C:/Windows/System32/WindowsSandbox.exe")
            if exe_path.exists():
                return  # Sandbox executable found - feature is installed

            # Fallback: try 'where' command
            try:
                result = subprocess.run(
                    ["where", "WindowsSandbox.exe"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    return  # Sandbox is available
            except:
                pass

            # If we get here, sandbox is not available
            raise RuntimeError("Windows Sandbox no está instalado. Verifique que la característica esté habilitada.")

        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise RuntimeError(f"No se pudo verificar Windows Sandbox: {e}")

    def _generate_wsb_config(self, workspace: Path, command: list[str], mapped_folders: list[tuple[Path, Path]]) -> str:
        """
        Genera archivo de configuración .wsb conforme a la especificación oficial Microsoft:
        https://learn.microsoft.com/es-es/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file

        Elementos soportados: vGPU, Networking, MappedFolders, LogonCommand, AudioInput,
        VideoInput, ProtectedClient, PrinterRedirection, ClipboardRedirection, MemoryInMB.
        """
        # Mapped folders: list of (host_path, sandbox_path)
        # IMPORTANTE: HostFolder debe ser ruta absoluta (no se admiten relativas según docs)
        mapped = []
        for host, sandbox in mapped_folders:
            host_abs = host.resolve()  # Forzar ruta absoluta
            mapped.append(f"""        <MappedFolder>
            <HostFolder>{host_abs}</HostFolder>
            <SandboxFolder>{sandbox}</SandboxFolder>
            <ReadOnly>false</ReadOnly>
        </MappedFolder>""")

        network = "Enable" if self.network else "Disable"

        # LogonCommand: un solo comando; casos complejos requieren script mapeado
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in command)

        return f"""<Configuration>
    <vGPU>Disable</vGPU>
    <Networking>{network}</Networking>
    <MappedFolders>
{chr(10).join(mapped)}
    </MappedFolders>
    <LogonCommand>
        <Command>cmd /c {cmd_str}</Command>
    </LogonCommand>
    <MemoryInMB>{max(2048, self.memory_mb)}</MemoryInMB>
    <AudioInput>Disable</AudioInput>
    <VideoInput>Disable</VideoInput>
    <ProtectedClient>Enable</ProtectedClient>
    <PrinterRedirection>Disable</PrinterRedirection>
    <ClipboardRedirection>Disable</ClipboardRedirection>
</Configuration>"""

    def execute(self,
                workspace: Path,
                command: list[str],
                timeout: int = 120,
                extra_maps: list[tuple[Path, Path]] = None) -> SandboxResult:
        """
        Ejecuta un comando dentro de Windows Sandbox.

        Args:
            workspace: Directorio del proyecto (se mapea como C:\\workspace)
            command: Comando a ejecutar (ej: ["python", "-m", "unittest"])
            timeout: Timeout en segundos
            extra_maps: Mapeos adicionales (host_path, sandbox_path)

        Returns:
            SandboxResult con stdout, stderr, returncode
        """
        # Preparar mapeos
        workspace = workspace.resolve()
        mapped = [(workspace, r"C:\workspace")]
        if extra_maps:
            mapped.extend(extra_maps)

        # Generar archivo .wsb temporal
        wsb_id = uuid.uuid4().hex[:8]
        wsb_path = Path(tempfile.gettempdir()) / f"exesoft_{wsb_id}.wsb"

        try:
            wsb_content = self._generate_wsb_config(workspace, command, mapped)
            wsb_path.write_text(wsb_content, encoding="utf-8")

            # Ejecutar Windows Sandbox
            # Nota: Windows Sandbox no tiene una forma nativa de capturar stdout/stderr fácilmente
            # Usamos un archivo de salida compartido
            output_file = workspace / f".sandbox_output_{wsb_id}.json"

            # Modificar comando para escribir resultado a archivo compartido
            wrapped_cmd = [
                "cmd", "/c",
                f"{command[0]} {' '.join(command[1:])} > {output_file.as_posix()} 2>&1"
                if len(command) > 1 else
                f"{command[0]} > {output_file.as_posix()} 2>&1"
            ]

            wsb_content = self._generate_wsb_config(workspace, wrapped_cmd, mapped)
            wsb_path.write_text(wsb_content, encoding="utf-8")

            # Ejecutar sandbox (bloqueante)
            result = subprocess.run(
                ["WindowsSandbox.exe", str(wsb_path)],
                capture_output=True,
                text=True,
                timeout=timeout + 30,  # Extra time for sandbox startup
                cwd=str(workspace)
            )

            # Leer resultado del archivo compartido
            stdout = ""
            stderr = ""
            returncode = 1

            if output_file.exists():
                try:
                    # El archivo contiene la salida del comando
                    stdout = output_file.read_text(encoding="utf-8", errors="replace")
                    # Intentar parsear returncode si está al final
                    lines = stdout.strip().split("\n")
                    if lines and lines[-1].startswith("__EXIT_CODE__:"):
                        returncode = int(lines[-1].split(":")[1])
                        stdout = "\n".join(lines[:-1])
                except Exception:
                    pass
            else:
                stdout = result.stdout
                stderr = result.stderr
                returncode = result.returncode

            return SandboxResult(
                success=(returncode == 0),
                stdout=stdout[-24000:],  # Limitar tamaño
                stderr=stderr[-24000:],
                returncode=returncode
            )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                stdout="",
                stderr=f"Timeout después de {timeout}s",
                returncode=-1,
                error="Timeout"
            )
        except Exception as e:
            return SandboxResult(
                success=False,
                stdout="",
                stderr=str(e),
                returncode=-1,
                error=type(e).__name__
            )
        finally:
            # Cleanup
            try:
                if wsb_path.exists():
                    wsb_path.unlink()
            except Exception:
                pass
            try:
                output_file = workspace / f".sandbox_output_{wsb_id}.json"
                if output_file.exists():
                    output_file.unlink()
            except Exception:
                pass


class SandboxExecutionPolicy:
    """
    Extensión de ExecutionPolicy que usa Windows Sandbox como backend real.
    Mantiene política fail-closed: si no hay sandbox, deniega todo.
    """

    def __init__(self, adapter: Optional[WindowsSandboxAdapter] = None):
        self.adapter = adapter
        self._available = adapter is not None

    @property
    def available(self) -> bool:
        return self._available

    def evaluate(self, *, trusted_code: bool = False, sandbox_attested: bool = False,
                 requested_network: bool = False, requested_host_paths: bool = False) -> "ExecutionDecision":
        from engine.execution_sandbox import ExecutionDecision

        if requested_host_paths:
            return ExecutionDecision(False, 'Acceso a rutas del host prohibido')
        if requested_network and not self._available:
            return ExecutionDecision(False, 'Red externa requiere sandbox habilitado')
        if not trusted_code:
            if not self._available:
                return ExecutionDecision(False, 'No hay aislamiento de procesos validado; ejecución de código no confiable bloqueada')
            # Con sandbox disponible, permitir código no confiable
            return ExecutionDecision(True, 'Ejecución autorizada en Windows Sandbox', isolation='windows_sandbox')
        # Trusted code is always allowed (with or without sandbox)
        return ExecutionDecision(True, 'Código de confianza autorizado', isolation='trusted')

    def require(self, **kwargs) -> None:
        from engine.execution_sandbox import ExecutionDenied
        decision = self.evaluate(**kwargs)
        if not decision.allowed:
            raise ExecutionDenied(decision.reason)