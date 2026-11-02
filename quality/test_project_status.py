import subprocess, json, sys
import tempfile
from pathlib import Path


def test_project_status_cli():
    # Set up isolated workspace
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name)
    # Import from project root
    project_root = Path(__file__).resolve().parents[1]
    sys.path.append(str(project_root))
    from engine.orchestrator import Orchestrator
    app = Orchestrator(workspace)
    pid = app.create_project('Demo', 'Test proyecto')
    app.plan_skeleton(pid)
    # Invoke factory CLI with --root argument BEFORE the subcommand
    # argparse requires parent args before positional subcommand args
    result = subprocess.run([sys.executable, '-m', 'factory', '--root', str(workspace), 'status', pid],
                            cwd=str(project_root), capture_output=True, text=True, check=True)
    out = json.loads(result.stdout)
    assert out['project']['id'] == pid
    assert len(out['tasks']) == 6  # pm, architect, ux, developer, qa, devops
    # At least one task should be ready (the first one)
    assert any(t['status'] == 'pending' for t in out['tasks'])
    # Clean up
    tmp.cleanup()
