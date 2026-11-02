"""Command‑line tool to query the current state of a project.

Usage:
    python -m project_status <project_id>

It loads the Orchestrator (which in turn loads the Brain SQLite DB) and
outputs a JSON document containing:
  * project metadata
  * list of all tasks with their status
  * list of tasks ready to be executed (dependencies satisfied)
"""

import sys
import json
from pathlib import Path

# Import from the package root (this file lives in the repo root)
sys.path.append(str(Path(__file__).resolve().parent))

from engine.orchestrator import Orchestrator

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m project_status <project_id>")
        sys.exit(1)
    project_id = sys.argv[1]
    app = Orchestrator(Path(__file__).resolve().parent)
    try:
        project = app.brain.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        tasks = app.brain.tasks(project_id)
        ready = app.ready_tasks(project_id)
        out = {
            "project": project,
            "tasks": tasks,
            "ready": ready,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
