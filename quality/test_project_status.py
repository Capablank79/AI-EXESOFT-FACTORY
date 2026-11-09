import subprocess, json, sys
import tempfile
import os
import uuid
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())


def test_project_status_cli():
    # Set up isolated workspace
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name)
    # Import from project root
    project_root = Path(__file__).resolve().parents[1]
    sys.path.append(str(project_root))
    from engine.orchestrator import Orchestrator
    tenant_id = uuid.UUID('b66f93cd-2f3a-4215-a2ac-93a59a84b4d2')
    app = Orchestrator(workspace, tenant_id=tenant_id)
    pid = app.create_project('Demo', 'Test proyecto')
    app.plan_skeleton(pid)
    # Invoke factory CLI with --root argument BEFORE the subcommand
    # argparse requires parent args before positional subcommand args
    env = os.environ.copy()
    env['EXESOFT_TENANT_ID'] = str(tenant_id)
    result = subprocess.run([sys.executable, '-m', 'factory', '--root', str(workspace), 'status', pid],
                            cwd=str(project_root), capture_output=True, text=True, check=True, env=env)
    out = json.loads(result.stdout)
    assert out['project']['id'] == pid
    assert len(out['tasks']) == 6  # pm, architect, ux, developer, qa, devops
    # At least one task should be ready (the first one)
    assert any(t['status'] == 'pending' for t in out['tasks'])
    # Clean up
    tmp.cleanup()
