"""v0.6: fail-closed execution policy and isolated Git worktrees.

A worktree isolates *version-control changes*, NOT process/filesystem/network access.
No untrusted code is executed by this module. A future OS sandbox adapter must
supply independent enforcement before permitting command execution.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ExecutionDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    isolation: str = 'none'


class ExecutionPolicy:
    """Deliberately rejects all untrusted process execution until v0.7 backend."""

    def evaluate(self, *, trusted_code: bool = False, sandbox_attested: bool = False,
                 requested_network: bool = False, requested_host_paths: bool = False) -> ExecutionDecision:
        if requested_host_paths:
            return ExecutionDecision(False, 'Acceso a rutas del host prohibido')
        if requested_network:
            return ExecutionDecision(False, 'Red externa prohibida para ejecución no confiable')
        if not trusted_code:
            return ExecutionDecision(False, 'No hay aislamiento de procesos validado; ejecución de código no confiable bloqueada')
        # Even attestation flags are not sufficient: only an actual backend
        # that enforces isolation can ever authorize execution.
        return ExecutionDecision(False, 'No existe adaptador OS sandbox habilitado')

    def require(self, **kwargs) -> None:
        decision = self.evaluate(**kwargs)
        if not decision.allowed:
            raise ExecutionDenied(decision.reason)


def _git(root: Path, *args: str) -> str:
    # Disable external/user git configuration and hooks. No shell expansion.
    env = dict(os.environ)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    p = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull,
                        '-c', 'protocol.file.allow=never', '-C', str(root), *args],
                       capture_output=True, text=True, timeout=20, env=env,
                       stdin=subprocess.DEVNULL)
    if p.returncode:
        raise RuntimeError('git (' + args[0] + '): ' + p.stderr.strip()[:350])
    return p.stdout.strip()


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def create_isolated_worktree(repository: str | Path, worktrees_root: str | Path,
                             task_id: str) -> Path:
    """Create detached worktree for a task, without writing to the main checkout.

    Requires an existing committed Git repository and a dedicated *empty* target.
    Does not run user code, tests, package installs, or generated commands.
    """
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id):
        raise ValueError('Identificador de tarea inválido')
    repo = Path(repository).resolve(strict=True)
    root = Path(worktrees_root).resolve(strict=True)
    if not repo.is_dir() or not root.is_dir() or repo == root:
        raise ValueError('Rutas inválidas')
    # Refuse a worktree root inside the source checkout: avoid recursive Git
    # scans and source modifications. Also reject a source inside target root.
    if _within(root, repo) or _within(repo, root):
        raise ValueError('Origen y destino deben estar separados')
    # Git must attest this is a checkout root, not a parent or child path.
    reported = Path(_git(repo, 'rev-parse', '--show-toplevel')).resolve(strict=True)
    if reported != repo:
        raise ValueError('La ruta debe ser la raíz del repositorio')
    _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}')
    target = root / ('task-' + task_id)
    if target.exists() or target.is_symlink():
        raise FileExistsError('Ya existe el espacio de trabajo')
    try:
        _git(repo, 'worktree', 'add', '--detach', '--', str(target), 'HEAD')
        if not target.is_dir():
            raise RuntimeError('Git no creó el worktree')
        return target
    except Exception:
        # Never recursively delete an unknown or attacker-owned path here.
        raise


def worktree_status(worktree: str | Path) -> str:
    path = Path(worktree).resolve(strict=True)
    if not path.is_dir() or not (path / '.git').is_file():
        raise ValueError('No es un worktree Git separado')
    return _git(path, 'status', '--porcelain', '--untracked-files=normal')
