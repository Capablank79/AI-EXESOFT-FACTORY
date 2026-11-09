"""Versioned requirements-to-plan contract and deterministic reference mapping."""
from __future__ import annotations

import hashlib
import json
import re


SCHEMA_VERSION = 'exf.plan.v1'
ALLOWED_AGENTS = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')
MAX_SPECS = 50
MAX_TASKS = 200
MAX_REQUIREMENTS = 1000
MAX_COVERAGE_EDGES = 1000
ACCEPTANCE_METHODS = frozenset({
    'unit_test', 'integration_test', 'api_test', 'ui_test',
    'database_check', 'security_test', 'manual_review',
})
VERIFICATION_ACTIONS = frozenset({'submit', 'invoke', 'query', 'inspect', 'execute'})
EXPECTED_RESULT_OPERATORS = frozenset({
    'equals', 'not_equals', 'contains', 'not_contains', 'count_equals',
    'exists', 'not_exists', 'matches', 'state_is',
})

# This is the model response contract. Prompt generation and validation consume
# the same object; persistence mapping uses the same version and field constants.
PLANNER_RESPONSE_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': 'EXESOFT Factory requirements plan',
    'type': 'object',
    'additionalProperties': False,
    'required': ['schema_version', 'specs', 'tasks', 'coverage'],
    'properties': {
        'schema_version': {'const': SCHEMA_VERSION},
        'specs': {
            'type': 'array', 'minItems': 1, 'maxItems': MAX_SPECS,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['ref', 'title', 'description', 'acceptance'],
                'properties': {
                    'ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                    'title': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                    'description': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                    'acceptance': {
                        'type': 'array', 'minItems': 1, 'maxItems': 20,
                        'items': {
                            'type': 'object', 'additionalProperties': False,
                            'required': ['description', 'verification', 'expected_result'],
                            'properties': {
                                'description': {'type': 'string', 'minLength': 8, 'maxLength': 500},
                                'verification': {
                                    'type': 'object', 'additionalProperties': False,
                                    'required': ['method', 'steps'],
                                    'properties': {
                                        'method': {'type': 'string', 'enum': sorted(ACCEPTANCE_METHODS)},
                                        'steps': {
                                            'type': 'array', 'minItems': 1, 'maxItems': 10,
                                            'items': {
                                                'type': 'object', 'additionalProperties': False,
                                                'required': ['action', 'target', 'input'],
                                                'properties': {
                                                    'action': {'type': 'string', 'enum': sorted(VERIFICATION_ACTIONS)},
                                                    'target': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                                                    'input': {'type': ['object', 'array'], 'minItems': 1},
                                                },
                                            },
                                        },
                                    },
                                },
                                'expected_result': {
                                    'type': 'object', 'additionalProperties': False,
                                    'required': ['target', 'operator', 'value'],
                                    'properties': {
                                        'target': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                                        'operator': {'type': 'string', 'enum': sorted(EXPECTED_RESULT_OPERATORS)},
                                        'value': {},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        'tasks': {
            'type': 'array', 'minItems': 1, 'maxItems': MAX_TASKS,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['ref', 'spec_ref', 'title', 'description', 'agent', 'depends_on'],
                'properties': {
                    'ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                    'spec_ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                    'title': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                    'description': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                    'agent': {'type': 'string', 'enum': list(ALLOWED_AGENTS)},
                    'depends_on': {
                        'type': 'array', 'uniqueItems': True,
                        'items': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                    },
                },
            },
        },
        'coverage': {
            'type': 'array', 'minItems': 1, 'maxItems': MAX_COVERAGE_EDGES,
            'uniqueItems': True,
            'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['requirement_id', 'spec_ref'],
                'properties': {
                    'requirement_id': {'type': 'string', 'pattern': '^REQ-[0-9a-f]{64}$'},
                    'spec_ref': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                },
            },
        },
    },
}

ROOT_FIELDS = frozenset(PLANNER_RESPONSE_SCHEMA['required'])
SPEC_FIELDS = frozenset(PLANNER_RESPONSE_SCHEMA['properties']['specs']['items']['required'])
TASK_FIELDS = frozenset(PLANNER_RESPONSE_SCHEMA['properties']['tasks']['items']['required'])
COVERAGE_FIELDS = frozenset(PLANNER_RESPONSE_SCHEMA['properties']['coverage']['items']['required'])
CRITERION_FIELDS = frozenset(
    PLANNER_RESPONSE_SCHEMA['properties']['specs']['items']['properties']['acceptance']
    ['items']['required'])
VERIFICATION_FIELDS = frozenset(
    PLANNER_RESPONSE_SCHEMA['properties']['specs']['items']['properties']['acceptance']
    ['items']['properties']['verification']['required'])
STEP_FIELDS = frozenset(
    PLANNER_RESPONSE_SCHEMA['properties']['specs']['items']['properties']['acceptance']
    ['items']['properties']['verification']['properties']['steps']['items']['required'])
EXPECTED_RESULT_FIELDS = frozenset(
    PLANNER_RESPONSE_SCHEMA['properties']['specs']['items']['properties']['acceptance']
    ['items']['properties']['expected_result']['required'])


class PlanningContractError(ValueError):
    """Aggregated, sanitized plan contract diagnostics."""

    def __init__(self, diagnostics):
        self.diagnostics = tuple(diagnostics)
        summary = '; '.join(f"{item['path']}:{item['code']}" for item in self.diagnostics)
        super().__init__(f'planning_contract_invalid[{summary}]')


def normalize_requirement(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text)).strip().casefold()


def build_requirement_catalog(requirements):
    """Assign stable Factory-owned IDs; duplicate normalized text is one requirement."""
    result = []
    seen_text, seen_ids = set(), {}
    for raw in requirements:
        text = str(raw).strip()
        normalized = normalize_requirement(text)
        if not normalized or normalized in seen_text:
            continue
        requirement_id = 'REQ-' + hashlib.sha256(normalized.encode('utf-8')).hexdigest()
        if requirement_id in seen_ids and seen_ids[requirement_id] != normalized:
            raise ValueError('Factory requirement ID collision')
        seen_text.add(normalized)
        seen_ids[requirement_id] = normalized
        result.append({'id': requirement_id, 'text': text})
    return result


def parse_planner_response(response):
    """Parse Gateway response without lossy remapping or leaking model text."""
    structured = getattr(response, 'structured', None)
    if structured is not None:
        if not isinstance(structured, dict):
            raise PlanningContractError([{'path': '$', 'code': 'expected_object'}])
        return structured
    try:
        content = response.content
    except (AttributeError, TypeError) as exc:
        raise PlanningContractError([{'path': '$', 'code': 'missing_json_content'}]) from exc
    if not isinstance(content, str):
        raise PlanningContractError([{'path': '$', 'code': 'expected_json_text'}])
    # Removing a single conventional JSON fence is a lossless structural normalization.
    candidate = content.strip()
    if candidate.startswith('```json') and candidate.endswith('```'):
        candidate = candidate[7:-3].strip()
    elif candidate.startswith('```') and candidate.endswith('```'):
        candidate = candidate[3:-3].strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise PlanningContractError([{'path': '$', 'code': 'malformed_json'}]) from exc
    if not isinstance(parsed, dict):
        raise PlanningContractError([{'path': '$', 'code': 'expected_object'}])
    return parsed


def _nonempty_bounded(value, minimum, maximum):
    return isinstance(value, str) and minimum <= len(value.strip()) <= maximum


def validate_and_map_plan(plan, requirement_catalog, allowed_agents):
    """Validate complete draft once, then deterministically map local refs to Factory IDs."""
    diagnostics = []

    def error(path, code):
        diagnostics.append({'path': path, 'code': code})

    if not isinstance(plan, dict):
        raise PlanningContractError([{'path': '$', 'code': 'expected_object'}])
    if set(plan) != ROOT_FIELDS:
        error('$', 'missing_or_unexpected_fields')
    if plan.get('schema_version') != SCHEMA_VERSION:
        error('$.schema_version', 'unsupported_version')

    expected_requirements = {item['id']: item for item in requirement_catalog}
    if not 1 <= len(requirement_catalog) <= MAX_REQUIREMENTS:
        error('$.requirement_catalog', 'invalid_catalog_size')

    specs = plan.get('specs')
    if not isinstance(specs, list) or not 1 <= len(specs) <= MAX_SPECS:
        error('$.specs', 'invalid_array_size')
        specs = []
    spec_ref_to_id = {}
    valid_specs = []
    for index, spec in enumerate(specs, 1):
        path = f'$.specs[{index}]'
        if not isinstance(spec, dict):
            error(path, 'expected_object')
            continue
        if set(spec) != SPEC_FIELDS:
            error(path, 'missing_or_unexpected_fields')
        ref = spec.get('ref')
        if not _nonempty_bounded(ref, 1, 80):
            error(path + '.ref', 'invalid_ref')
        elif ref in spec_ref_to_id:
            error(path + '.ref', 'duplicate_ref')
        else:
            spec_ref_to_id[ref] = f'S{index:03d}'
        if not _nonempty_bounded(spec.get('title'), 1, 1000):
            error(path + '.title', 'invalid_text')
        if not _nonempty_bounded(spec.get('description'), 1, 1000):
            error(path + '.description', 'invalid_text')
        acceptance = spec.get('acceptance')
        if not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 20:
            error(path + '.acceptance', 'invalid_array_size')
            acceptance = []
        mapped_criteria = []
        for criterion_index, criterion in enumerate(acceptance, 1):
            cpath = f'{path}.acceptance[{criterion_index}]'
            fields = CRITERION_FIELDS
            if not isinstance(criterion, dict):
                error(cpath, 'expected_structured_object')
                continue
            if set(criterion) != fields:
                error(cpath, 'missing_or_unexpected_fields')
            if not _nonempty_bounded(criterion.get('description'), 8, 500):
                error(cpath + '.description', 'invalid_text')

            verification = criterion.get('verification')
            if not isinstance(verification, dict) or set(verification) != VERIFICATION_FIELDS:
                error(cpath + '.verification', 'expected_method_and_steps')
                verification = {}
            method = verification.get('method')
            if not isinstance(method, str) or method not in ACCEPTANCE_METHODS:
                error(cpath + '.verification.method', 'unsupported_method')
            steps = verification.get('steps')
            if not isinstance(steps, list) or not 1 <= len(steps) <= 10:
                error(cpath + '.verification.steps', 'invalid_array_size')
                steps = []
            mapped_steps = []
            for step_index, step in enumerate(steps, 1):
                spath = f'{cpath}.verification.steps[{step_index}]'
                if not isinstance(step, dict) or set(step) != STEP_FIELDS:
                    error(spath, 'expected_action_target_input')
                    continue
                action, target, input_value = step.get('action'), step.get('target'), step.get('input')
                if not isinstance(action, str) or action not in VERIFICATION_ACTIONS:
                    error(spath + '.action', 'unsupported_action')
                if not _nonempty_bounded(target, 1, 200):
                    error(spath + '.target', 'invalid_text')
                try:
                    encoded_input = json.dumps(input_value, ensure_ascii=False, allow_nan=False)
                except (TypeError, ValueError):
                    encoded_input = ''
                if (not isinstance(input_value, (dict, list)) or not input_value
                        or encoded_input in {'', '[]', '{}'} or len(encoded_input) > 500):
                    error(spath + '.input', 'invalid_input')
                mapped_steps.append({'action': action, 'target': target, 'input': input_value})

            expected = criterion.get('expected_result')
            if not isinstance(expected, dict) or set(expected) != EXPECTED_RESULT_FIELDS:
                error(cpath + '.expected_result', 'expected_target_operator_value')
                expected = {}
            result_target = expected.get('target')
            operator, value = expected.get('operator'), expected.get('value')
            if not _nonempty_bounded(result_target, 1, 200):
                error(cpath + '.expected_result.target', 'invalid_text')
            if not isinstance(operator, str) or operator not in EXPECTED_RESULT_OPERATORS:
                error(cpath + '.expected_result.operator', 'unsupported_operator')
            try:
                encoded_value = json.dumps(value, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                encoded_value = ''
            if (value is None or encoded_value in {'', '""', '[]', '{}'}
                    or len(encoded_value) > 500):
                error(cpath + '.expected_result.value', 'invalid_value')
            mapped_criteria.append({
                'description': criterion.get('description'),
                'verification': {'method': method, 'steps': mapped_steps},
                'expected_result': {'target': result_target, 'operator': operator, 'value': value},
            })
        valid_specs.append({
            'id': f'S{index:03d}', 'title': spec.get('title'),
            'description': spec.get('description'), 'acceptance': mapped_criteria,
        })

    tasks = plan.get('tasks')
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        error('$.tasks', 'invalid_array_size')
        tasks = []
    task_ref_to_id = {}
    valid_tasks = []
    for index, task in enumerate(tasks, 1):
        path = f'$.tasks[{index}]'
        if not isinstance(task, dict):
            error(path, 'expected_object')
            continue
        if set(task) != TASK_FIELDS:
            error(path, 'missing_or_unexpected_fields')
        ref = task.get('ref')
        if not _nonempty_bounded(ref, 1, 80):
            error(path + '.ref', 'invalid_ref')
        elif ref in task_ref_to_id:
            error(path + '.ref', 'duplicate_ref')
        else:
            task_ref_to_id[ref] = f'T{index:03d}'
        spec_ref = task.get('spec_ref')
        if not isinstance(spec_ref, str) or spec_ref not in spec_ref_to_id:
            error(path + '.spec_ref', 'unknown_spec_ref')
        if not _nonempty_bounded(task.get('title'), 1, 2000):
            error(path + '.title', 'invalid_text')
        if not _nonempty_bounded(task.get('description'), 1, 2000):
            error(path + '.description', 'invalid_text')
        agent = task.get('agent')
        if not isinstance(agent, str) or agent not in allowed_agents:
            error(path + '.agent', 'unsupported_agent')
        dependencies = task.get('depends_on')
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            error(path + '.depends_on', 'expected_ref_array')
            dependencies = []
        elif len(dependencies) != len(set(dependencies)):
            error(path + '.depends_on', 'duplicate_dependency_ref')
        valid_tasks.append({
            'id': f'T{index:03d}', 'model_ref': ref,
            'spec_id': spec_ref_to_id.get(spec_ref) if isinstance(spec_ref, str) else None,
            'title': task.get('title'),
            'description': task.get('description'), 'agent': agent,
            'depends_on': dependencies,
        })

    task_refs = {item.get('ref') for item in tasks if isinstance(item, dict)
                 and isinstance(item.get('ref'), str)}
    graph = {}
    used_specs = set()
    for index, task in enumerate(tasks, 1):
        path = f'$.tasks[{index}].depends_on'
        if not isinstance(task, dict):
            continue
        ref = task.get('ref')
        dependencies = task.get('depends_on')
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            continue
        if isinstance(ref, str) and ref:
            graph[ref] = dependencies
        spec_ref = task.get('spec_ref')
        if isinstance(spec_ref, str):
            used_specs.add(spec_ref)
        for dep in dependencies:
            if dep not in task_refs:
                error(path, 'unknown_task_ref')
            elif dep == ref:
                error(path, 'self_dependency')
    for index, spec in enumerate(specs, 1):
        if (isinstance(spec, dict) and isinstance(spec.get('ref'), str)
                and spec.get('ref') not in used_specs):
            error(f'$.specs[{index}].ref', 'spec_has_no_tasks')

    visiting, visited = set(), set()
    def visit(ref):
        if ref in visiting:
            return True
        if ref in visited:
            return False
        visiting.add(ref)
        cycle = any(visit(dep) for dep in graph.get(ref, ()) if dep in graph)
        visiting.remove(ref)
        visited.add(ref)
        return cycle
    if any(visit(ref) for ref in graph):
        error('$.tasks', 'dependency_cycle')

    coverage = plan.get('coverage')
    if not isinstance(coverage, list) or not 1 <= len(coverage) <= MAX_COVERAGE_EDGES:
        error('$.coverage', 'invalid_array_size')
        coverage = []
    seen_edges, covered_requirements = set(), set()
    edges_by_requirement = {}
    for index, edge in enumerate(coverage, 1):
        path = f'$.coverage[{index}]'
        if not isinstance(edge, dict) or set(edge) != COVERAGE_FIELDS:
            error(path, 'expected_requirement_spec_edge')
            continue
        requirement_id, spec_ref = edge.get('requirement_id'), edge.get('spec_ref')
        if not isinstance(requirement_id, str) or requirement_id not in expected_requirements:
            error(path + '.requirement_id', 'unknown_requirement_id')
        if not isinstance(spec_ref, str) or spec_ref not in spec_ref_to_id:
            error(path + '.spec_ref', 'unknown_spec_ref')
        pair = None
        if isinstance(requirement_id, str) and isinstance(spec_ref, str):
            pair = (requirement_id, spec_ref)
            if pair in seen_edges:
                error(path, 'duplicate_coverage_edge')
            else:
                seen_edges.add(pair)
        if (isinstance(requirement_id, str) and requirement_id in expected_requirements
                and isinstance(spec_ref, str) and spec_ref in spec_ref_to_id):
            covered_requirements.add(requirement_id)
            edges_by_requirement.setdefault(requirement_id, set()).add(spec_ref)
    for requirement_id in expected_requirements:
        if requirement_id not in covered_requirements:
            error('$.coverage', 'requirement_not_covered')

    if diagnostics:
        raise PlanningContractError(diagnostics)

    mapped_tasks = []
    for task in valid_tasks:
        mapped_tasks.append({
            'id': task['id'], 'spec_id': task['spec_id'], 'title': task['title'],
            'description': task['description'], 'agent': task['agent'],
            'depends_on': [task_ref_to_id[ref] for ref in task['depends_on']],
        })
    mapped_coverage = []
    for requirement in requirement_catalog:
        mapped_coverage.append({
            'requirement_id': requirement['id'],
            'requirement': requirement['text'],
            'spec_ids': [spec_ref_to_id[ref]
                         for ref in sorted(edges_by_requirement[requirement['id']])],
        })
    return {
        'schema_version': SCHEMA_VERSION,
        'specs': valid_specs,
        'tasks': mapped_tasks,
        'requirement_coverage': mapped_coverage,
    }


def planner_schema_prompt():
    return json.dumps(PLANNER_RESPONSE_SCHEMA, ensure_ascii=False, sort_keys=True)
