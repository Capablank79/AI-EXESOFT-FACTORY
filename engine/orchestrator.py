"""Núcleo de planificación persistente con ODD integrado."""
from pathlib import Path
from uuid import uuid4
import json
from brain.store import Brain
from engine.odd import ODDEngine, Spec, Task, FeatureDocument

ROLES=('pm','architect','ux','developer','qa','devops')  # Added UX for design review

class Orchestrator:
    def __init__(self, root):
        self.root=Path(root)
        self.brain=Brain(self.root/'brain'/'brain.db')
        self.odd = ODDEngine(self.brain)

    def create_project(self, name, request):
        if not name.strip() or not request.strip():
            raise ValueError('Nombre y solicitud son obligatorios')
        pid=uuid4().hex[:12]
        folder=self.root/'workspaces'/pid
        folder.mkdir(parents=True,exist_ok=False)
        self.brain.add_project(pid,name,request)
        (folder/'request.md').write_text('# '+name+'\n\n'+request+'\n',encoding='utf-8')
        # Initialize ODD feature document
        self.odd.create_feature(pid, name, request)
        return pid

    def plan_skeleton(self, pid):
        """Create ODD-based plan: Specs + Tasks with acceptance criteria."""
        project=self.brain.get_project(pid)
        if not project:
            raise KeyError(pid)
        if self.brain.tasks(pid):
            raise ValueError('El proyecto ya tiene tareas; no duplicar planificación')

        # S1-S2: Create Specs with acceptance criteria
        spec1 = self.odd.add_spec(pid, "Alcance y Aceptación",
            "Definir alcance funcional, criterios de aceptación y límites del proyecto.",
            ["Alcance documentado en Brain", "Criterios de aceptación medibles", "Riesgos identificados"])
        spec2 = self.odd.add_spec(pid, "Arquitectura",
            "Definir arquitectura técnica, componentes, interfaces y decisiones de diseño.",
            ["Diagrama de componentes", "Interfaces definidas", "Decisiones registradas"])
        spec3 = self.odd.add_spec(pid, "Diseño UI/UX",
            "Diseñar interfaz de usuario y experiencia de usuario según requisitos.",
            ["Mockups aprobados", "Especificaciones de interacción", "Accesibilidad verificada"])
        spec4 = self.odd.add_spec(pid, "Implementación",
            "Desarrollar código según arquitectura, diseño y alcance, con pruebas unitarias.",
            ["Código implementado", "Pruebas unitarias pasando", "Evidencia SHA256 registrada"])
        spec5 = self.odd.add_spec(pid, "Validación QA",
            "Ejecutar pruebas de integración, validar evidencia y aprobar/rechazar entrega.",
            ["Pruebas de integración pasando", "Evidencia verificada", "Decisión QA registrada"])
        spec6 = self.odd.add_spec(pid, "Entrega",
            "Preparar artefactos de entrega, documentación y registro final.",
            ["Artefactos empaquetados", "Documentación completa", "Estado done en Brain"])

        # S4: Create Tasks linked to Specs with routing (6 agents now)
        odd_tasks = [
            self.odd.add_task(pid, spec1.id, "pm", "Definir alcance y aceptación"),
            self.odd.add_task(pid, spec2.id, "architect", "Definir arquitectura"),
            self.odd.add_task(pid, spec3.id, "ux", "Diseñar UI/UX"),
            self.odd.add_task(pid, spec4.id, "developer", "Implementar según alcance"),
            self.odd.add_task(pid, spec5.id, "qa", "Validar mediante pruebas"),
            self.odd.add_task(pid, spec6.id, "devops", "Preparar entrega local"),
        ]

        # Persist legacy tasks table for backward compatibility with Runtime (6 agents)
        tasks_data = [
            ("Definir alcance y aceptación", "pm"),
            ("Definir arquitectura", "architect"),
            ("Diseñar UI/UX", "ux"),
            ("Implementar según alcance", "developer"),
            ("Validar mediante pruebas", "qa"),
            ("Preparar entrega local", "devops"),
        ]
        previous = None
        for (title, agent), odd_task in zip(tasks_data, odd_tasks):
            tid = uuid4().hex[:12]
            self.brain.add_task(tid, pid, title, agent, [previous] if previous else [])
            self.brain.link_odd_task(pid, odd_task.id, tid)
            previous = tid

        self.brain.set_project_status(pid, 'planned')
        return self.brain.tasks(pid)

    def ready_tasks(self, pid):
        tasks = self.brain.tasks(pid)
        done = {t['id'] for t in tasks if t['status'] == 'done'}
        return [t for t in tasks if t['status'] == 'pending' and t['attempts'] < t['max_attempts']
                and all(dep in done for dep in json.loads(t['depends_on']))]

    def get_odd_feature(self, pid) -> FeatureDocument | None:
        """Obtener documento ODD para consultas avanzadas."""
        return self.odd.load_feature(pid)
