"""Exesoft v0.5: máquina de ejecución con quality gate obligatorio y auditoría.

Sin ejecución automática de código no confiable. No habilita escrituras a Claude Code.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from engine.agent_runtime import AgentRuntime
from engine.agents import SandyDeveloperAdapter, SandyQAAdapter
from engine.runtime import Result, now
from engine.quality_gate import QualityGate
from quality.evidence import verify_test_evidence
from engine.odd import ODDEngine


class AutonomousEngine(AgentRuntime):
    """Ejecuta tareas no destructivas y valida QA con evidencias registradas por operador confiable.

    Reutiliza el planificador y su persistencia. La aceptación final del proyecto
    queda pendiente de una puerta de calidad; no confundir respuesta IA con QA real.
    """

    def __init__(self, root, adapters=None):
        super().__init__(root, adapters=adapters)
        self.gate = QualityGate(self.root)
        self.odd = ODDEngine(self.brain)
        self._init_quality_schema()

    def _init_quality_schema(self):
        with self.brain.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS quality_artifacts (
                project_id TEXT NOT NULL REFERENCES projects(id),
                artifact_path TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                PRIMARY KEY(project_id, artifact_path)
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS quality_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id),
                task_id TEXT NOT NULL REFERENCES tasks(id),
                approved INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )''')

    def register_test_evidence(self, project_id, relative_path):
        """Registro supervisado. La evidencia debe haber sido generada por harness confiable.

        La validación SHA256 en v0.4 detecta alteraciones accidentales, no constituye
        firma criptográfica ni prueba origen independiente; no ejecutar tests de IA
        en el host fuera de sandbox.
        """
        if not self.brain.get_project(project_id):
            raise KeyError(project_id)
        workspace = (self.root / 'workspaces' / project_id).resolve()
        if not workspace.is_relative_to((self.root / 'workspaces').resolve()):
            raise ValueError('Workspace fuera de ruta autorizada')
        if not verify_test_evidence(workspace, relative_path):
            raise ValueError('Evidencia inválida o prueba no aprobada')
        with self.brain.connect() as db:
            db.execute('INSERT OR IGNORE INTO quality_artifacts(project_id,artifact_path,registered_at) VALUES (?,?,?)',
                       (project_id, relative_path, now()))
            db.execute('INSERT INTO events(project_id,type,detail,created_at) VALUES (?,?,?,?)',
                       (project_id, 'quality.evidence.registered', relative_path, now()))

    def _get_artifacts(self, project_id):
        with self.brain.connect() as db:
            return [r['artifact_path'] for r in db.execute(
                'SELECT artifact_path FROM quality_artifacts WHERE project_id=? ORDER BY registered_at,artifact_path',
                (project_id,))]

    def _record_decision(self, project_id, task_id, decision):
        with self.brain.connect() as db:
            db.execute('INSERT INTO quality_decisions(project_id,task_id,approved,reason,created_at) VALUES (?,?,?,?,?)',
                       (project_id,task_id,int(decision['approved']),decision['reason'],now()))
            db.execute('INSERT INTO events(project_id,task_id,type,detail,created_at) VALUES (?,?,?,?,?)',
                       (project_id,task_id,'quality.approved' if decision['approved'] else 'quality.rejected',
                        decision['reason'],now()))

    def _dispatch(self, task, project):
        if task['agent'] != 'qa':
            result = super()._dispatch(task, project)
            if task['agent'] == 'developer' and result.success and isinstance(self.adapters.get('developer'), SandyDeveloperAdapter):
                if not result.evidence:
                    return Result(False, 'Developer Sandy no registró evidencia de pruebas')
                try:
                    for ref in result.evidence:
                        self.register_test_evidence(project['id'], ref)
                except (ValueError, OSError) as exc:
                    return Result(False, f'Evidencia developer rechazada: {exc}', result.evidence)
            return result
        # Antes de invocar QA, exigir evidencia real válida.
        artifacts = self._get_artifacts(project['id'])
        preliminary = self.gate.evaluate(project['id'], Result(True, 'preflight'), artifacts)
        if not preliminary['approved']:
            self._record_decision(project['id'],task['id'],preliminary)
            return Result(False, 'Quality Gate: '+preliminary['reason'])
        result = super()._dispatch(task, project)
        if result.success and isinstance(self.adapters.get('qa'), SandyQAAdapter):
            if not result.evidence:
                result = Result(False, 'QA Sandy no registró evidencia de pruebas')
        if result.success and result.evidence:
            try:
                for ref in result.evidence:
                    if verify_test_evidence(self.root / 'workspaces' / project['id'], ref):
                        self.register_test_evidence(project['id'], ref)
                    elif isinstance(self.adapters.get('qa'), SandyQAAdapter):
                        raise ValueError('La evidencia QA Sandy no es verificable')
            except (ValueError, OSError) as exc:
                result = Result(False, f'Evidencia QA rechazada: {exc}', result.evidence)
            artifacts = self._get_artifacts(project['id'])
        # Habilitar corrección autónoma: si QA falla pero hay evidencia parcial
        if not result.success:
            test_artifacts = artifacts
            if test_artifacts:
                partial = self._is_partial_evidence(test_artifacts, project['id'])
                if partial:
                    self._record_decision(project['id'],task['id'],
                        {'approved': False, 'reason': 'Requiere corrección automática'})
                    return Result(False, 'Quality Gate: requiere corrección', tuple(test_artifacts))
        decision = self.gate.evaluate(project['id'], result, artifacts)
        if not result.approved:
            decision = {'approved': False, 'reason': 'QA requiere aprobación supervisada explícita'}
        self._record_decision(project['id'],task['id'],decision)
        if not decision['approved']:
            return Result(False, 'Quality Gate: '+decision['reason'])
        return Result(True, result.summary, tuple(artifacts), approved=True)

    def _is_partial_evidence(self, test_artifacts, project_id=None):
        """Check if test artifacts allow partial results (correction workflow)."""
        try:
            from quality.evidence import verify_test_evidence
            for p in test_artifacts:
                if verify_test_evidence(self.root / 'workspaces' / project_id / '', p):
                    return True
            return False
        except Exception:
            return False

    def run_controlled(self, project_id, max_steps=10):
        """Ejecución limitada: exige verificar recuperación manual tras un corte.

        Evita invocar Runtime.run(), que recuperaría automáticamente tareas en curso
        antes de determinar si dejaron efectos externos.
        """
        if not 1 <= max_steps <= 100:
            raise ValueError('max_steps fuera de rango')
        project = self.brain.get_project(project_id)
        if project is None:
            raise KeyError(project_id)
        if any(t['status'] == 'running' for t in self.brain.tasks(project_id)):
            return [{'state':'manual_recovery_required','task':None}]
        history = []
        for _ in range(max_steps):
            outcome = self.step(project_id)
            history.append(outcome)
            if outcome['state'] in ('blocked','cancelled','concurrent_update') or outcome['task'] is None:
                break
            if outcome['state'] == 'pending':
                break  # no repetir una solicitud externa inmediatamente
        return history

    def decisions(self, project_id):
        with self.brain.connect() as db:
            return [dict(r) for r in db.execute(
                'SELECT * FROM quality_decisions WHERE project_id=? ORDER BY id',(project_id,))]
