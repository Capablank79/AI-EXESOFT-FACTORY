"""Isolated git workspaces; does not modify existing repositories or run hooks."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess


def initialize_workspace(workspace):
    root=Path(workspace).resolve(strict=True)
    if (root/'.git').exists():
        raise ValueError('Repositorio existente: no se inicializa ni modifica')
    env={**os.environ,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull}
    p=subprocess.run(['git','-c','core.hooksPath='+os.devnull,'init','-q','--',str(root)],
                     capture_output=True,text=True,timeout=15,env=env)
    if p.returncode: raise RuntimeError('git init falló: '+p.stderr[:400])
    return root


def git_status(workspace):
    root=Path(workspace).resolve(strict=True)
    if not (root/'.git').is_dir(): raise ValueError('No existe repositorio aislado')
    p=subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-C',str(root),
                      'status','--porcelain','--untracked-files=normal'],
                     capture_output=True,text=True,timeout=15)
    if p.returncode: raise RuntimeError(p.stderr[:400])
    return p.stdout
