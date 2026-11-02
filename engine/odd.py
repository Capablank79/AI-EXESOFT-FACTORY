"""
ODD — Organic Driven Development (Implementación Nativa Python)

Basado en los principios de Gentle-AI ODD:
- S1: Routing canónico por especificación
- S2: Documento de característica (Spec → Tasks → Log)
- S3: Cambios proporcionales (solo spec/tarea afectada)
- S4: Handoffs por referencia (nunca paraphrase)
- S5: Verificación ejecuta ejemplos de spec
- S6: Agentes especializados

Integración con EXESOFT:
- Brain SQLite = Persistencia de Specs/Tasks/Log
- Orchestrator = Routing y ejecución
- QualityGate = Verificación S5
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4
import json


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Spec:
    """S1-S2: Especificación atómica (S#)."""
    id: str                    # S001, S002...
    title: str
    description: str
    acceptance: list[str]      # Criterios de aceptación (user strings verbatim)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def to_markdown(self) -> str:
        lines = [f"## Spec {self.id}: {self.title}", "", self.description, ""]
        if self.acceptance:
            lines.append("### Acceptance Criteria")
            for i, ac in enumerate(self.acceptance, 1):
                lines.append(f"- AC{i}: {ac}")
        return "\n".join(lines)


@dataclass
class Task:
    """S2-S4: Tarea vinculada a Spec (T# → S#)."""
    id: str                    # T001, T002...
    spec_id: str               # Referencia a Spec
    route: str                 # pm|architect|developer|qa|devops
    description: str
    status: str = "pending"    # pending|running|done|blocked|failed
    evidence: list[str] = field(default_factory=list)
    commit_sha: Optional[str] = None
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)


@dataclass
class FeatureDocument:
    """
    S2: Documento de característica completo.
    Header → Specs → Tasks → Log
    """
    project_id: str
    name: str
    request: str
    specs: list[Spec] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)  # L1 original, correcciones, evidencia
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def add_spec(self, title: str, description: str, acceptance: list[str]) -> Spec:
        used = {int(s.id[1:]) for s in self.specs if s.id.startswith('S') and s.id[1:].isdigit()}
        number = 1
        while number in used: number += 1
        spec_id = f"S{number:03d}"
        spec = Spec(id=spec_id, title=title, description=description, acceptance=acceptance)
        self.specs.append(spec)
        self.updated_at = now()
        return spec

    def add_task(self, spec_id: str, route: str, description: str) -> Task:
        used = {int(t.id[1:]) for t in self.tasks if t.id.startswith('T') and t.id[1:].isdigit()}
        number = 1
        while number in used: number += 1
        task_id = f"T{number:03d}"
        task = Task(id=task_id, spec_id=spec_id, route=route, description=description)
        self.tasks.append(task)
        self.updated_at = now()
        return task

    def log_event(self, event_type: str, detail: str, spec_id: str = "", task_id: str = ""):
        self.log.append({
            "type": event_type,
            "detail": detail,
            "spec_id": spec_id,
            "task_id": task_id,
            "timestamp": now()
        })
        self.updated_at = now()

    def get_ready_tasks(self) -> list[Task]:
        """S4: Obtener tareas listas (dependencias satisfechas)."""
        done_specs = {t.spec_id for t in self.tasks if t.status == "done"}
        return [t for t in self.tasks
                if t.status == "pending" and t.spec_id in done_specs]

    def to_markdown(self) -> str:
        lines = [
            f"# {self.name}",
            f"**Project:** {self.project_id}",
            f"**Request:** {self.request}",
            "",
            "## Specs",
        ]
        for s in self.specs:
            lines.append(s.to_markdown())
            lines.append("")
        lines.append("## Tasks")
        for t in self.tasks:
            lines.append(f"- **{t.id}** (→{t.spec_id}) [{t.route}] {t.description} — *{t.status}*")
        lines.append("")
        lines.append("## Log")
        for entry in self.log:
            lines.append(f"- [{entry['timestamp']}] {entry['type']}: {entry['detail']}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "project_id": self.project_id,
            "name": self.name,
            "request": self.request,
            "specs": [s.__dict__ for s in self.specs],
            "tasks": [t.__dict__ for t in self.tasks],
            "log": self.log,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, data: str) -> "FeatureDocument":
        d = json.loads(data)
        fd = cls(d["project_id"], d["name"], d["request"])
        fd.specs = [Spec(**s) for s in d.get("specs", [])]
        fd.tasks = [Task(**t) for t in d.get("tasks", [])]
        fd.log = d.get("log", [])
        fd.created_at = d.get("created_at", now())
        fd.updated_at = d.get("updated_at", now())
        return fd


class ODDEngine:
    """
    Motor ODD integrado con Brain SQLite.
    Persiste FeatureDocuments en tabla knowledge (kind='odd_spec').
    """

    def __init__(self, brain):
        self.brain = brain

    def create_feature(self, project_id: str, name: str, request: str) -> FeatureDocument:
        """Crear documento de característica vacío."""
        fd = FeatureDocument(project_id=project_id, name=name, request=request)
        self._persist(project_id, fd)
        return fd

    def load_feature(self, project_id: str) -> Optional[FeatureDocument]:
        """Cargar documento de característica desde Brain."""
        body = self.brain.get_odd_document(project_id)
        if body:
            try:
                current = FeatureDocument.from_json(body)
                if current.specs or current.tasks or not self.brain.odd_snapshots(project_id): return current
            except (ValueError, TypeError, KeyError): pass
        # Upgrade legacy append-only snapshots without deleting historical rows.
        snapshots = self.brain.odd_snapshots(project_id)
        merged = None
        spec_ids = {}
        for row in snapshots:
            try: fragment = FeatureDocument.from_json(row['body'])
            except (ValueError, TypeError, KeyError): continue
            if merged is None:
                merged = FeatureDocument(fragment.project_id, fragment.name, fragment.request,
                                         created_at=fragment.created_at)
            if fragment.name: merged.name = fragment.name
            if fragment.request: merged.request = fragment.request
            for spec in fragment.specs:
                key = (spec.title, spec.description)
                existing = next((s for s in merged.specs if (s.title,s.description)==key), None)
                if existing is None:
                    existing = merged.add_spec(spec.title,spec.description,list(spec.acceptance))
                    existing.created_at, existing.updated_at = spec.created_at, spec.updated_at
                spec_ids[(row['id'],spec.id)] = existing.id
            for task in fragment.tasks:
                existing = next((t for t in merged.tasks if (t.route,t.description)==(task.route,task.description)), None)
                if existing is None:
                    target_spec = spec_ids.get((row['id'],task.spec_id))
                    if not target_spec and merged.specs:
                        idx = len(merged.tasks)
                        target_spec = merged.specs[min(idx,len(merged.specs)-1)].id
                    if not target_spec: target_spec = task.spec_id
                    existing = merged.add_task(target_spec,task.route,task.description)
                existing.status = task.status
                existing.evidence = list(task.evidence)
                existing.commit_sha = task.commit_sha
                existing.updated_at = task.updated_at
            for entry in fragment.log:
                if entry not in merged.log: merged.log.append(entry)
            merged.updated_at = fragment.updated_at
        if merged:
            self.brain.persist_odd_document(project_id, f"Feature: {merged.name}", merged.to_json())
        return merged

    def _persist(self, project_id: str, fd: FeatureDocument):
        """Guardar en Brain como conocimiento ODD."""
        body = fd.to_json()
        self.brain.persist_odd_document(project_id, f"Feature: {fd.name}", body)

    def ensure_task_links(self, project_id: str):
        fd = self.load_feature(project_id)
        if not fd: return
        brain_tasks = self.brain.tasks(project_id)
        for odd_task in fd.tasks:
            if self.brain.brain_task_for_odd(project_id, odd_task.id): continue
            match = next((task for task in brain_tasks
                          if task['agent']==odd_task.route and task['title']==odd_task.description), None)
            if match: self.brain.link_odd_task(project_id, odd_task.id, match['id'])

    def sync_brain_task(self, project_id: str, brain_task_id: str, status: str, evidence=None):
        self.ensure_task_links(project_id)
        odd_task_id = self.brain.odd_task_for_brain(project_id, brain_task_id)
        if odd_task_id:
            self.update_task_status(project_id, odd_task_id, status, evidence=evidence)

    def add_spec(self, project_id: str, title: str, description: str, acceptance: list[str]) -> Spec:
        fd = self.load_feature(project_id) or self.create_feature(project_id, "", "")
        spec = fd.add_spec(title, description, acceptance)
        fd.log_event("spec_added", f"Added spec {spec.id}: {title}", spec_id=spec.id)
        self._persist(project_id, fd)
        return spec

    def add_task(self, project_id: str, spec_id: str, route: str, description: str) -> Task:
        fd = self.load_feature(project_id)
        if not fd:
            raise ValueError(f"No feature document for project {project_id}")
        task = fd.add_task(spec_id, route, description)
        fd.log_event("task_added", f"Added task {task.id} for spec {spec_id}", spec_id=spec_id, task_id=task.id)
        self._persist(project_id, fd)
        return task

    def update_task_status(self, project_id: str, task_id: str, status: str, evidence: list[str] = None, commit_sha: str = None):
        if status not in {'pending','running','done','blocked','failed'}: raise ValueError('Estado ODD inválido')
        fd = self.load_feature(project_id)
        if not fd:
            raise ValueError(f"No feature document for project {project_id}")
        for task in fd.tasks:
            if task.id == task_id:
                if task.status == status and (not evidence or task.evidence == evidence) and (not commit_sha or task.commit_sha == commit_sha): return
                task.status = status
                if evidence:
                    task.evidence = evidence
                if commit_sha:
                    task.commit_sha = commit_sha
                task.updated_at = now()
                fd.log_event("task_status", f"Task {task_id} → {status}", task_id=task_id)
                self._persist(project_id, fd)
                return
        raise KeyError(task_id)

    def get_next_task(self, project_id: str) -> Optional[Task]:
        """S4: Siguiente tarea ejecutable según routing proporcional."""
        fd = self.load_feature(project_id)
        if not fd:
            return None
        ready = fd.get_ready_tasks()
        return ready[0] if ready else None

    def verify_spec(self, project_id: str, spec_id: str) -> bool:
        """S5: Verificar que todos los criterios de aceptación de una spec se cumplen."""
        fd = self.load_feature(project_id)
        if not fd:
            return False
        spec = next((s for s in fd.specs if s.id == spec_id), None)
        if not spec:
            return False
        # Verificar que todas las tareas de la spec están done con evidencia
        spec_tasks = [t for t in fd.tasks if t.spec_id == spec_id]
        return all(t.status == "done" and t.evidence for t in spec_tasks)
