"""Exesoft v0.3.1: one supervised Brain -> Runtime -> Router -> Brain cycle.
No source edits, external commands, or automatic multi-step execution.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from engine.agent_runtime import AgentRuntime
from engine.agents import FreeLLMAPIAdapter

DEFAULT_MODEL = 'gpt-oss-120b'

@dataclass(frozen=True)
class CycleReport:
    project_id: str
    task_id: str
    run_id: str
    state: str
    model_requested: str
    knowledge_id: int | None
    summary: str


def supervised_cycle(root, api_key: str, model: str = DEFAULT_MODEL, *, request: str | None = None):
    """Perform exactly one text-only PM task, persist outcome and retrieve persisted record.

    Explicit human invocation only. No credentials are persisted. On a failed call the
    task remains governed by the existing runtime retry rules, but is NOT retried here.
    """
    if not api_key or any(ord(c) < 33 or ord(c) == 127 for c in api_key):
        raise ValueError('Clave del router vacía o con caracteres de control/espacio')
    if model != DEFAULT_MODEL:
        raise ValueError('Esta verificación solo admite el modelo ya comprobado: ' + DEFAULT_MODEL)
    task_request = request or ('Verifica el estado de la arquitectura Exesoft AI Factory. '
                               'Describe en español dos próximos pasos técnicos concretos '
                               'para conectar Brain y Orquestador, sin inventar pruebas ni cambios.')
    if len(task_request) > 2000 or not task_request.strip():
        raise ValueError('Solicitud inválida')
    adapter = FreeLLMAPIAdapter(api_key=api_key, model=model, timeout=90)
    runtime = AgentRuntime(root, adapters={'pm': adapter})
    pid = runtime.orchestrator.create_project('Validación de integración del núcleo', task_request)
    task_id = uuid4().hex[:12]
    runtime.brain.add_task(task_id, pid, 'Verificar integración Brain-Orquestador', 'pm', max_attempts=1)
    runtime.brain.set_project_status(pid, 'planned')
    # Retrieval is based on terms in task title; initial knowledge is project-specific.
    runtime.brain.remember('architecture', 'Verificar integración Brain Orquestador',
                           'Brain utiliza SQLite FTS5; Runtime persiste estado de tareas y ejecuciones.',
                           'arquitectura instalada v0.1.1-v0.3', pid)
    outcome = runtime.step(pid)
    knowledge_id = None
    if outcome['state'] == 'done':
        knowledge_id = runtime.brain.remember('execution_result', 'Respuesta de integración',
                                             outcome['summary'], 'FreeLLMAPI/' + model, pid)
    # Verify persisted run, rather than treating the response of step() as proof of persistence.
    persisted = runtime.runs(pid)
    if len(persisted) != 1 or persisted[0]['id'] != outcome['run']:
        raise RuntimeError('No se encontró la ejecución persistida')
    row = persisted[0]
    if row['summary'] != outcome['summary']:
        raise RuntimeError('La respuesta persistida difiere del resultado')
    if knowledge_id is not None:
        with runtime.brain.connect() as db:
            k = db.execute('SELECT body,source FROM knowledge WHERE id=? AND project_id=?',
                           (knowledge_id, pid)).fetchone()
        if not k or k['body'] != row['summary']:
            raise RuntimeError('No se pudo confirmar la persistencia en Brain')
    return CycleReport(pid, task_id, row['id'], row['status'], model, knowledge_id, row['summary'])
