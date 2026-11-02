"""Exesoft v0.2: ejecución persistente con adaptadores explícitos y control QA.
No ejecuta comandos arbitrarios, no invoca modelos sin configuración.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from dataclasses import dataclass
from brain.store import Brain
from engine.orchestrator import Orchestrator
from engine.odd import ODDEngine


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Result:
    success: bool
    summary: str
    evidence: tuple[str, ...] = ()
    approved: bool = False


class ManualAdapter:
    """Modo seguro: sin ejecución de terceros, devuelve un bloqueo explícito."""
    def execute(self, task, project, context):
        return Result(False, 'Adapter no conectado: se requiere ejecución real del agente')


class Runtime:
    def __init__(self, root, adapters=None):
        self.root = Path(root)
        self.orchestrator = Orchestrator(self.root)
        self.brain = self.orchestrator.brain
        self.adapters = adapters or {}
        self.odd = ODDEngine(self.brain)
        self._init_schema()

    def _init_schema(self):
        with self.brain.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS task_runs (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    agent TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted')),
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary TEXT,
                    evidence TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);
            ''')

    def recover(self, project_id=None):
        """Reclasifica ejecuciones huérfanas; no reintenta ciegamente acciones externas."""
        count = 0
        recovered = []
        with self.brain.connect() as db:
            query = "SELECT t.id,t.project_id,t.attempts,t.max_attempts FROM tasks t WHERE t.status='running'"
            args = []
            if project_id:
                query += ' AND t.project_id=?'
                args.append(project_id)
            rows = db.execute(query, args).fetchall()
            for task in rows:
                status = 'pending' if task['attempts'] < task['max_attempts'] else 'failed'
                db.execute('UPDATE tasks SET status=?, updated_at=? WHERE id=?', (status, now(), task['id']))
                db.execute("UPDATE task_runs SET status='interrupted',finished_at=? WHERE task_id=? AND status='running'", (now(),task['id']))
                db.execute('INSERT INTO events(project_id,task_id,type,detail,created_at) VALUES (?,?,?,?,?)',
                           (task['project_id'],task['id'],'task.recovered',status,now()))
                recovered.append((task['project_id'],task['id'],status))
                count += 1
        for pid, task_id, status in recovered:
            self.odd.sync_brain_task(pid, task_id, status)
        return count

    def _project(self, pid):
        project = self.brain.get_project(pid)
        if not project:
            raise KeyError(pid)
        return project

    def _dispatch(self, task, project):
        adapter = self.adapters.get(task['agent'])
        if adapter is None:
            return Result(False, 'Sin adaptador registrado para ' + task['agent'])
        context = {'workspace':str(self.root / 'workspaces' / project['id']),
                   'request':project['request'], 'task_id':task['id']}
        return adapter.execute(task, project, context)

    def step(self, pid):
        """Ejecuta máximo una tarea lista, con eventos transaccionales y límites de intentos."""
        project = self._project(pid)
        if project['status'] in ('done','cancelled','blocked'):
            return {'state':project['status'],'task':None}
        ready = self.orchestrator.ready_tasks(pid)
        if not ready:
            tasks = self.brain.tasks(pid)
            if tasks and all(t['status']=='done' for t in tasks):
                self.brain.set_project_status(pid,'done')
                return {'state':'done','task':None}
            if any(t['status']=='running' for t in tasks):
                return {'state':'running','task':None}
            self.brain.set_project_status(pid,'blocked')
            return {'state':'blocked','task':None}
        task = ready[0]
        self.odd.ensure_task_links(pid)
        rid = uuid4().hex
        with self.brain.connect() as db:
            db.execute("UPDATE tasks SET status='running', attempts=attempts+1,updated_at=? WHERE id=? AND status='pending'",
                       (now(),task['id']))
            if db.execute('SELECT changes()').fetchone()[0] != 1:
                return {'state':'concurrent_update','task':task['id']}
            db.execute('INSERT INTO task_runs(id,task_id,project_id,agent,status,started_at) VALUES (?,?,?,?,?,?)',
                       (rid,task['id'],pid,task['agent'],'running',now()))
            db.execute('INSERT INTO events(project_id,task_id,type,detail,created_at) VALUES (?,?,?,?,?)',
                       (pid,task['id'],'task.started',rid,now()))
        if project['status'] != 'running':
            self.brain.set_project_status(pid,'running')
        self.odd.sync_brain_task(pid, task['id'], 'running')
        try:
            result = self._dispatch(task,project)
            if not isinstance(result,Result):
                raise TypeError('El adaptador debe devolver Result')
        except Exception as exc:
            result = Result(False, f'Error del adaptador: {type(exc).__name__}: {exc}')
        if task['agent']=='qa' and result.success and (not result.approved or not result.evidence):
            result = Result(False,'QA requiere aprobación explícita y evidencias verificables',result.evidence)
        with self.brain.connect() as db:
            attempts = db.execute('SELECT attempts,max_attempts FROM tasks WHERE id=?',(task['id'],)).fetchone()
            task_status = 'done' if result.success else ('pending' if attempts['attempts'] < attempts['max_attempts'] else 'failed')
            db.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?',(task_status,now(),task['id']))
            db.execute('UPDATE task_runs SET status=?,finished_at=?,summary=?,evidence=? WHERE id=?',
                       ('succeeded' if result.success else 'failed',now(),result.summary,json.dumps(list(result.evidence)),rid))
            db.execute('INSERT INTO events(project_id,task_id,type,detail,created_at) VALUES (?,?,?,?,?)',
                       (pid,task['id'],'task.' + ('succeeded' if result.success else 'failed'),result.summary,now()))
        if task_status=='failed':
            self.brain.set_project_status(pid,'blocked')
        self.odd.sync_brain_task(pid, task['id'], task_status,
                                 evidence=list(result.evidence) if task_status == 'done' else None)
        return {'state':task_status,'task':task['id'],'agent':task['agent'],'run':rid,'summary':result.summary}

    def run(self,pid,max_steps=20):
        if max_steps < 1:
            raise ValueError('max_steps debe ser positivo')
        self.recover(pid)
        history=[]
        for _ in range(max_steps):
            outcome=self.step(pid)
            history.append(outcome)
            if outcome['state'] in ('blocked','cancelled','concurrent_update') or outcome['task'] is None:
                break
        return history

    def runs(self,pid):
        with self.brain.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM task_runs WHERE project_id=? ORDER BY started_at,id',(pid,))]
