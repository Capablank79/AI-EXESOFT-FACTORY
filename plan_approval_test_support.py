"""Explicit synthetic operator decisions for execution regression tests."""
import uuid
import json

from infrastructure.plan_approval import approval_status, record_plan_decision
from infrastructure.uow import UnitOfWork
from engine.model_gateway import ModelResponse


def approve_test_plan(tenant_id, project_id):
    project_id = uuid.UUID(str(project_id))
    with UnitOfWork(tenant_id=tenant_id) as uow:
        state = approval_status(uow._get_connection(), tenant_id, project_id)
        return record_plan_decision(
            uow, project_id, actor_id='synthetic-test-operator',
            expected_hash=state['plan_hash'], approve=True,
        )


class DeterministicPlannerGateway:
    """No-network planning response for CLI/API integration tests."""
    def complete(self, request):
        content = request.messages[-1]['content']
        from engine.planning_contract import SCHEMA_VERSION

        section = content.split('Authoritative requirement catalog:\n', 1)[1].split(
            '\nCanonical response JSON Schema:', 1
        )[0]
        try:
            catalog = json.loads(section)
        except (json.JSONDecodeError, TypeError):
            catalog = []
        if not isinstance(catalog, list):
            catalog = []
        agents = ('pm', 'architect', 'ux', 'developer', 'qa', 'devops')
        tasks = [
            {'ref': f'task_{index:03d}', 'spec_ref': 'project_scope', 'title': f'{agent} planning task',
             'description': f'Address project requirements as the {agent} role.',
             'agent': agent, 'depends_on': [f'task_{index - 1:03d}'] if index > 1 else []}
            for index, agent in enumerate(agents, 1)
        ]
        plan = {
            'schema_version': SCHEMA_VERSION,
            'specs': [{'ref': 'project_scope', 'title': 'Project plan',
                       'description': 'Validated project scope.',
                       'acceptance': [{
                           'description': 'All six agent tasks are persisted with valid references.',
                           'verification': {'method': 'database_check', 'steps': [{
                               'action': 'query', 'target': 'project.tasks',
                               'input': {'expected_task_count': 6},
                           }]},
                           'expected_result': {
                               'target': 'project.tasks.count', 'operator': 'count_equals', 'value': 6,
                           },
                       }]}],
            'tasks': tasks,
            'coverage': [
                {'requirement_id': item['id'], 'spec_ref': 'project_scope'}
                for item in catalog if isinstance(item, dict) and isinstance(item.get('id'), str)
            ],
        }
        return ModelResponse(content=json.dumps(plan), structured=plan)


def install_mock_planner(monkeypatch):
    import engine.orchestrator as orchestrator_module
    monkeypatch.setattr(
        orchestrator_module, '_configured_planning_gateway',
        lambda: (DeterministicPlannerGateway(), 1.0),
    )
