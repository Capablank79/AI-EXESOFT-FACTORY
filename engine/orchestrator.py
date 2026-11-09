"""Núcleo de planificación persistente con ODD integrado."""
from pathlib import Path
from uuid import uuid4
import json
import os
import re
from brain.store import Brain as SQLiteBrain
from brain.postgres_store import Brain as PostgresBrain
from engine.odd import ODDEngine, Spec, Task, FeatureDocument
from engine.model_gateway import ModelGatewayError, ModelRequest
from engine.planning_contract import (
    ALLOWED_AGENTS, SCHEMA_VERSION, PlanningContractError, build_requirement_catalog,
    parse_planner_response, planner_schema_prompt, validate_and_map_plan,
)

ROLES = ALLOWED_AGENTS


class PlanValidationError(ValueError):
    """The generated plan is malformed or does not cover stored requirements."""

    def __init__(self, message, diagnostics=()):
        self.diagnostics = tuple(diagnostics)
        super().__init__(message)


class PlanGatewayError(ValueError):
    """The configured Model Gateway is unavailable or timed out."""


class PlanConflictError(ValueError):
    """The project already has ODD or relational plan content."""


def _configured_planning_gateway():
    """Return the existing configured Gateway, without exposing credentials."""
    from engine.agents import GatewayModelAdapter, configured_model_adapter
    adapter = configured_model_adapter()
    if not isinstance(adapter, GatewayModelAdapter):
        raise PlanGatewayError('Configured Model Gateway is unavailable')
    return adapter.gateway, adapter.timeout


def _request_requirements(request):
    """Extract explicit requirement units from the persisted project request."""
    if isinstance(request, dict):
        raw = request.get('requirements')
        if isinstance(raw, list):
            candidates = [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
        else:
            request = request.get('description', request.get('request', ''))
            candidates = None
    else:
        candidates = None
    if candidates is None:
        text = request if isinstance(request, str) else str(request or '')
        lines = [re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", line).strip()
                 for line in text.splitlines()]
        lines = [line for line in lines if line]
        if len(lines) == 1:
            pieces = re.split(r";\s*|(?<=[.!?])\s+(?=[A-Z0-9])", lines[0])
            candidates = [piece.strip() for piece in pieces if piece.strip()]
        else:
            candidates = lines
    normalized, result = set(), []
    for item in candidates:
        key = re.sub(r"\s+", " ", item).strip().casefold()
        if key and key not in normalized:
            normalized.add(key)
            result.append(item)
    return result


def validate_requirements_plan(plan, requirements):
    """Validate a versioned planner response and map local refs to Factory IDs."""
    catalog = (requirements if requirements and isinstance(requirements[0], dict)
               and {'id', 'text'} <= set(requirements[0])
               else build_requirement_catalog(requirements or ()))
    try:
        return validate_and_map_plan(plan, catalog, ROLES)
    except PlanningContractError as exc:
        raise PlanValidationError(str(exc), diagnostics=exc.diagnostics) from exc

class Orchestrator:
    def __init__(self, root, tenant_id=None, brain=None, backend='postgres', planning_gateway=None):
        self.root=Path(root)
        if backend not in {'sqlite', 'postgres'}:
            raise ValueError("backend must be 'sqlite' or 'postgres'")
        if backend == 'postgres' and tenant_id is None:
            tenant_id = os.environ.get('EXESOFT_TENANT_ID')
            if tenant_id is None:
                raise ValueError('Pass tenant_id or set EXESOFT_TENANT_ID for PostgreSQL persistence')
        self.brain=brain or (
            PostgresBrain(tenant_id=tenant_id) if backend == 'postgres'
            else SQLiteBrain(self.root/'brain'/'brain.db')
        )
        self.odd = ODDEngine(self.brain)
        self.planning_gateway = planning_gateway

    def create_project(self, name, request):
        if not name.strip() or not request.strip():
            raise ValueError('Nombre y solicitud son obligatorios')
        pid=str(uuid4())
        folder=self.root/'workspaces'/pid
        self.brain.add_project(pid,name,request)
        folder.mkdir(parents=True,exist_ok=False)
        (folder/'request.md').write_text('# '+name+'\n\n'+request+'\n',encoding='utf-8')
        # Initialize ODD feature document
        self.odd.create_feature(pid, name, request)
        return pid

    def plan_from_requirements(self, pid, gateway=None, *, replan_authorized_hash=None):
        """Generate and validate a plan, then persist it atomically in PostgreSQL."""
        project = self.brain.get_project(pid)
        if not project:
            raise KeyError(pid)
        project_value = dict(project) if isinstance(project, dict) else vars(project).copy()
        request = project_value.get('request', '')
        if isinstance(request, dict):
            requirements = _request_requirements(request)
        else:
            requirements = _request_requirements(request)
        if not requirements:
            raise PlanValidationError('Stored project request contains no requirements to plan')
        if replan_authorized_hash is not None and (
                not isinstance(replan_authorized_hash, str)
                or not re.fullmatch(r'[0-9a-f]{64}', replan_authorized_hash)):
            raise PlanValidationError('Replan authorization must be a lowercase SHA-256 fingerprint')
        preflight = getattr(self.brain, 'planning_preflight', None)
        if preflight is not None:
            existing = preflight(
                pid, request, replan_authorized_hash=replan_authorized_hash,
            )
            if existing is not None:
                return existing

        selected_gateway = gateway if gateway is not None else self.planning_gateway
        timeout = 90.0
        if selected_gateway is None:
            selected_gateway, timeout = _configured_planning_gateway()
        elif hasattr(selected_gateway, 'timeout'):
            timeout = selected_gateway.timeout

        catalog = build_requirement_catalog(requirements)
        if not catalog:
            raise PlanValidationError('Stored project request contains no requirements to plan')
        prompt = (
            'Create a project-specific plan from the persisted requirements. Do not execute work, '
            'call tools, write code, or approve the plan. Return only one JSON object conforming '
            f'exactly to schema version {SCHEMA_VERSION}. The JSON Schema below is authoritative. '
            'Requirements and their IDs are Factory-owned and authoritative: cover every ID at '
            'least once using unique requirement_id/spec_ref edges. A requirement may map to several '
            'specs and a spec may cover several requirements. Never change, invent, omit, or echo '
            'requirement text in place of IDs. Use your own unique local spec refs and task refs; '
            'use only those refs for spec_ref and dependency links. The Factory resolves refs to '
            'its stable IDs. Ensure every spec has at least one task, every task references a known '
            'spec, and dependencies are acyclic.\n'
            'Authoritative requirement catalog:\n'
            + json.dumps(catalog, ensure_ascii=False, sort_keys=True)
            + '\nCanonical response JSON Schema:\n' + planner_schema_prompt()
        )
        metadata = {
            'tenant_id': str(project_value.get('tenant_id') or getattr(self.brain, 'tenant_id', '')),
            'project_id': str(project_value.get('id') or pid),
            'agent': 'pm', 'operation': 'project_planning',
        }
        request_model = ModelRequest(
            messages=[
                {'role': 'system', 'content': 'You are the EXESOFT Factory planning engine. Return valid JSON only.'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=6000, timeout=timeout, response_format='json',
            required_capabilities=frozenset({'text', 'json'}), metadata=metadata,
        )

        def call_gateway(model_request):
            try:
                return selected_gateway.complete(model_request)
            except (ModelGatewayError, TimeoutError, OSError, ValueError, TypeError) as exc:
                raise PlanGatewayError(
                    f'Model Gateway planning request failed ({type(exc).__name__})'
                ) from exc

        response = call_gateway(request_model)
        try:
            plan = validate_requirements_plan(parse_planner_response(response), catalog)
        except (PlanningContractError, PlanValidationError) as first_error:
            diagnostics = getattr(first_error, 'diagnostics', ())
            sanitized = [
                {'path': item['path'], 'code': item['code']}
                for item in diagnostics
            ] or [{'path': '$', 'code': 'invalid_plan'}]
            repair_request = ModelRequest(
                messages=[
                    {'role': 'system', 'content':
                     'Repair the plan once to satisfy the Factory planning schema. Return JSON only. '
                     'Do not invent requirements or coverage; use only the supplied requirement IDs. '
                     'Do not execute work or approve the plan.'},
                    {'role': 'user', 'content':
                     'Regenerate a complete plan for the same requirements using only the schema, '
                     'catalog and sanitized diagnostics below. The previous draft is intentionally '
                     'omitted. Preserve semantic coverage and provide no unsupported claims.\n'
                     + prompt + '\nValidation diagnostics:\n'
                     + json.dumps(sanitized, ensure_ascii=False, sort_keys=True)},
                ],
                max_tokens=6000, timeout=timeout, response_format='json',
                required_capabilities=frozenset({'text', 'json'}), metadata=metadata,
            )
            response = call_gateway(repair_request)
            try:
                plan = validate_requirements_plan(parse_planner_response(response), catalog)
            except (PlanningContractError, PlanValidationError) as repair_error:
                repair_diagnostics = getattr(repair_error, 'diagnostics', ())
                raise PlanValidationError(
                    str(repair_error), diagnostics=repair_diagnostics,
                ) from repair_error

        persist = getattr(self.brain, 'persist_validated_plan', None)
        if persist is None:
            raise RuntimeError('Requirements planning persistence requires the PostgreSQL Brain')
        return persist(
            pid, plan, requirements, request,
            replan_authorized_hash=replan_authorized_hash,
        )

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
            tid = str(uuid4())
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
