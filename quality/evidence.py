"""Evidence is produced by trusted local execution, not by LLM claims.

Phase 1 (foundational updates):
- Create tests/ directory automatically if it doesn't exist
- Allow partial test results for correction workflows
- Verify SHA256 of stdout+stderr despite failures
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import re
from datetime import datetime, timezone
from uuid import uuid4


def _inside(root: Path, candidate: Path) -> Path:
    """Validate candidate path is inside root workspace."""
    root = root.resolve(strict=True)
    path = candidate.resolve()
    if not path.is_relative_to(root):
        raise ValueError('Ruta fuera del workspace autorizado')
    return path


def file_evidence(workspace, relative_path):
    root = Path(workspace).resolve(strict=True)
    path = _inside(root, root / relative_path)
    if not path.is_file():
        raise ValueError('No es un archivo')
    return {'kind': 'file', 'path': str(path.relative_to(root)),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size}


def verify_file(workspace, evidence):
    if evidence.get('kind') != 'file': return False
    try:
        real=file_evidence(workspace, evidence['path'])
        return real == evidence
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run_tests(workspace, *, timeout=90, partial_ok=False):
    """
    Runs unittest discovery from the project's tests/ directory.

    Args:
        workspace: Directory containing the project
        timeout: Maximum seconds to run tests (default: 90)
        partial_ok: If True, failed tests do not cause immediate rejection (correction workflow)

    Phase 1: Automatically creates tests/ directory if it doesn't exist.
    """
    root=Path(workspace).resolve(strict=True)
    # Create tests/ directory if missing (Phase 1)
    tests_dir = _inside(root, root/'tests')
    tests_dir.mkdir(parents=True, exist_ok=True)

    timeout=max(1,min(int(timeout),300))
    # Use sys.executable for proper Python path on Windows
    cmd=[sys.executable, '-I', '-m', 'unittest', 'discover', '-s', str(tests_dir),
         '-p', 'test_*.py', '-v']
    if not partial_ok:
        cmd.append('--failfast')

    try:
        proc=subprocess.run(cmd, cwd=root, capture_output=True, text=True, errors='replace',
                            timeout=timeout, stdin=subprocess.DEVNULL,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        status='passed' if proc.returncode==0 else 'failed'
        output=(proc.stdout+proc.stderr)[-24000:]  # Last 24KB enough for most failures
        code=proc.returncode
    except subprocess.TimeoutExpired as exc:
        status='timeout'; code=None
        def decode(s): return s.decode(errors='replace') if isinstance(s,bytes) else (s or '')
        output=(decode(exc.stdout)+'\n'+decode(exc.stderr))[-24000:]

    artifact={'schema': 'exesoft.quality.test.v1', 'id': uuid4().hex,
              'kind': 'test', 'status': status, 'returncode': code,
              'command': 'python -I -m unittest discover -s tests -p test_*.py -v',
              'output': output, 'output_sha256': hashlib.sha256(output.encode()).hexdigest(),
              'tests_run': _test_count(output),
              'partial_ok': partial_ok, 'timestamp': datetime.now(timezone.utc).isoformat()}
    return artifact


def _test_count(output):
    matches = re.findall(r'^Ran\s+(\d+)\s+tests?(?:\s+in\s+[^\r\n]+)?\s*$', output, re.MULTILINE)
    return int(matches[-1]) if matches else 0


def record_sandbox_test_evidence(workspace, command, result):
    """Persist the exact outcome of a Sandy-run test command for the Quality Gate."""
    stdout = getattr(result, 'stdout', '') or ''
    stderr = getattr(result, 'stderr', '') or ''
    output = (stdout + stderr)[-24000:]
    returncode = getattr(result, 'returncode', None)
    success = bool(getattr(result, 'success', False)) and returncode == 0
    artifact = {
        'schema': 'exesoft.quality.test.v1',
        'id': uuid4().hex,
        'kind': 'test',
        'runner': 'sandy',
        'status': 'passed' if success else 'failed',
        'returncode': returncode,
        'sandy_exit_code': getattr(result, 'sandy_exit_code', returncode),
        'error': str(getattr(result, 'error', ''))[:300],
        'command': list(command),
        'output': output,
        'output_sha256': hashlib.sha256(output.encode('utf-8')).hexdigest(),
        'tests_run': _test_count(output),
        'result_sha256': str(getattr(result, 'sha256', '')),
        'partial_ok': False,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    return record_evidence(workspace, artifact)


def record_evidence(workspace, artifact):
    root=Path(workspace).resolve(strict=True)
    evidence_dir=root/'.exesoft'/'evidence'
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if artifact.get('schema')!='exesoft.quality.test.v1': raise ValueError('Esquema desconocido')
    target=evidence_dir/(artifact['id']+'.json')
    with target.open('x', encoding='utf-8') as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2)
    return str(target.relative_to(root))


def verify_test_evidence(workspace, relative_path):
    """
    Verifies a test evidence JSON file.

    Only complete, successful runs with at least one executed test qualify for approval.

    Returns False for:
    - Non-existent files
    - Files not in .exesoft/evidence/
    - Malformed JSON
    - Unrecognized schemas
    - Tampered output (SHA256 mismatch)
    """
    try:
        root=Path(workspace).resolve(strict=True)
        p=_inside(root,root/relative_path)
        # Phase 1: Must be inside .exesoft/evidence directory
        if p.parent != (root/'.exesoft'/'evidence').resolve():
            return False
        data=json.loads(p.read_text(encoding='utf-8'))

        # Verify SHA256 integrity first
        output = data.get('output', '')
        stored_hash = data.get('output_sha256')
        if hashlib.sha256(output.encode()).hexdigest() != stored_hash:
            return False

        status=data.get('status')
        code=data.get('returncode')
        if data.get('runner') == 'sandy':
            digest = data.get('result_sha256')
            if (data.get('sandy_exit_code') != 0 or not isinstance(digest, str) or len(digest) != 64
                    or any(c not in '0123456789abcdefABCDEF' for c in digest)):
                return False
        # Legacy artifacts may lack tests_run; derive it from their hashed output.
        count = data.get('tests_run', _test_count(output))
        return status == 'passed' and type(code) is int and code == 0 and type(count) is int and count > 0
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        return False
