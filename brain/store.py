"""Exesoft Brain: SQLite transaccional + índice FTS5. Sin solicitudes externas."""
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import json
from datetime import datetime, timezone

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, request TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('created','planned','running','blocked','review','done','cancelled')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL, agent TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','blocked','failed','done')),
 depends_on TEXT NOT NULL DEFAULT '[]', attempts INTEGER NOT NULL DEFAULT 0,
 max_attempts INTEGER NOT NULL DEFAULT 3, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT REFERENCES projects(id),
 kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS odd_documents (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), body TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS odd_task_links (
 project_id TEXT NOT NULL REFERENCES projects(id), odd_task_id TEXT NOT NULL,
 brain_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
 PRIMARY KEY(project_id, odd_task_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, body, content='knowledge', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
 INSERT INTO knowledge_fts(rowid,title,body) VALUES(new.id,new.title,new.body);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
 INSERT INTO knowledge_fts(knowledge_fts,rowid,title,body) VALUES('delete',old.id,old.title,old.body);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge BEGIN
 INSERT INTO knowledge_fts(knowledge_fts,rowid,title,body) VALUES('delete',old.id,old.title,old.body);
 INSERT INTO knowledge_fts(rowid,title,body) VALUES(new.id,new.title,new.body);
END;
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id),
 task_id TEXT, type TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

def utc_now():
    return datetime.now(timezone.utc).isoformat()

class Brain:
    def __init__(self, db_path):
        self.db_path=Path(db_path)
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn=sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=15000')
        conn.execute('PRAGMA journal_mode=WAL')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add_project(self, project_id, name, request):
        now=utc_now()
        with self.connect() as db:
            db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',(project_id,name,request,'created',now,now))
            db.execute('INSERT INTO events(project_id,type,detail,created_at) VALUES (?,?,?,?)',(project_id,'project.created',request,now))

    def get_project(self, project_id):
        with self.connect() as db:
            row=db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
            return dict(row) if row else None

    def set_project_status(self, project_id, status):
        allowed={'created','planned','running','blocked','review','done','cancelled'}
        if status not in allowed: raise ValueError('Estado inválido')
        now=utc_now()
        with self.connect() as db:
            cur=db.execute('UPDATE projects SET status=?,updated_at=? WHERE id=?',(status,now,project_id))
            if not cur.rowcount: raise KeyError(project_id)
            db.execute('INSERT INTO events(project_id,type,detail,created_at) VALUES (?,?,?,?)',(project_id,'project.status',status,now))

    def add_task(self, task_id, project_id, title, agent, depends_on=(), max_attempts=3,
                 description=None):
        if max_attempts<1: raise ValueError('max_attempts debe ser positivo')
        now=utc_now()
        with self.connect() as db:
            db.execute('INSERT INTO tasks(id,project_id,title,agent,status,depends_on,attempts,max_attempts,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (task_id,project_id,title,agent,'pending',json.dumps(list(depends_on)),0,max_attempts,now))

    def tasks(self, project_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tasks WHERE project_id=? ORDER BY rowid',(project_id,))]

    def update_task(self, task_id, status, increment_attempts=False):
        if status not in {'pending','running','blocked','failed','done'}: raise ValueError('Estado inválido')
        with self.connect() as db:
            cur=db.execute('UPDATE tasks SET status=?, attempts=attempts+?, updated_at=? WHERE id=?',
                (status,int(increment_attempts),utc_now(),task_id))
            if not cur.rowcount: raise KeyError(task_id)

    def remember(self, kind, title, body, source, project_id=None):
        if not source.strip(): raise ValueError('Se requiere fuente de conocimiento')
        with self.connect() as db:
            cur=db.execute('INSERT INTO knowledge(project_id,kind,title,body,source,created_at) VALUES (?,?,?,?,?,?)',
                (project_id,kind,title,body,source,utc_now()))
            return cur.lastrowid

    def recall(self, query, project_id=None, limit=5):
        if not query.strip(): return []
        with self.connect() as db:
            sql="""SELECT k.id,k.project_id,k.kind,k.title,k.body,k.source FROM knowledge_fts f
                     JOIN knowledge k ON f.rowid=k.id WHERE knowledge_fts MATCH ?
                     AND (k.kind!='odd_spec' OR k.project_id IS NULL OR k.id=(
                         SELECT MAX(current.id) FROM knowledge current
                         WHERE current.kind='odd_spec' AND current.project_id=k.project_id))"""
            args=[query]
            if project_id is not None:
                sql+=' AND (k.project_id=? OR k.project_id IS NULL)'
                args.append(project_id)
            sql+=' ORDER BY rank LIMIT ?'
            args.append(max(1,min(limit,30)))
            return [dict(row) for row in db.execute(sql,args)]

    def get_odd_document(self, project_id):
        with self.connect() as db:
            row=db.execute('SELECT body FROM odd_documents WHERE project_id=?',(project_id,)).fetchone()
            return row['body'] if row else None

    def save_odd_document(self, project_id, body):
        with self.connect() as db:
            db.execute('INSERT INTO odd_documents(project_id,body,updated_at) VALUES (?,?,?) '
                       'ON CONFLICT(project_id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at',
                       (project_id,body,utc_now()))

    def persist_odd_document(self, project_id, title, body):
        """Atomically update the current document and append one historical snapshot."""
        now=utc_now()
        with self.connect() as db:
            row=db.execute('SELECT body FROM odd_documents WHERE project_id=?',(project_id,)).fetchone()
            if row and row['body']==body: return False
            db.execute('INSERT INTO odd_documents(project_id,body,updated_at) VALUES (?,?,?) '
                       'ON CONFLICT(project_id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at',
                       (project_id,body,now))
            db.execute('INSERT INTO knowledge(project_id,kind,title,body,source,created_at) VALUES (?,?,?,?,?,?)',
                       (project_id,'odd_spec',title,body,'ODDEngine',now))
            return True

    def persist_odd_document(self, project_id, title, body):
        """Atomically update the current document and append one historical snapshot."""
        now=utc_now()
        with self.connect() as db:
            row=db.execute('SELECT body FROM odd_documents WHERE project_id=?',(project_id,)).fetchone()
            if row and row['body']==body: return False
            db.execute('INSERT INTO odd_documents(project_id,body,updated_at) VALUES (?,?,?) '
                       'ON CONFLICT(project_id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at',
                       (project_id,body,now))
            db.execute('INSERT INTO knowledge(project_id,kind,title,body,source,created_at) VALUES (?,?,?,?,?,?)',
                       (project_id,'odd_spec',title,body,'ODDEngine',now))
            return True

    def odd_snapshots(self, project_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,body FROM knowledge WHERE project_id=? AND kind='odd_spec' ORDER BY id",(project_id,))]

    def link_odd_task(self, project_id, odd_task_id, brain_task_id):
        with self.connect() as db:
            db.execute('INSERT INTO odd_task_links(project_id,odd_task_id,brain_task_id) VALUES (?,?,?) '
                       'ON CONFLICT(project_id,odd_task_id) DO UPDATE SET brain_task_id=excluded.brain_task_id',
                       (project_id,odd_task_id,brain_task_id))

    def brain_task_for_odd(self, project_id, odd_task_id):
        with self.connect() as db:
            row=db.execute('SELECT brain_task_id FROM odd_task_links WHERE project_id=? AND odd_task_id=?',
                           (project_id,odd_task_id)).fetchone()
            return row['brain_task_id'] if row else None

    def odd_task_for_brain(self, project_id, brain_task_id):
        with self.connect() as db:
            row=db.execute('SELECT odd_task_id FROM odd_task_links WHERE project_id=? AND brain_task_id=?',
                           (project_id,brain_task_id)).fetchone()
            return row['odd_task_id'] if row else None
