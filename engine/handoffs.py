"""Validated, serializable handoff records between Factory agents."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class AgentHandoff:
    handoff_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_id: UUID
    run_id: UUID
    from_agent: str
    to_agents: tuple[str, ...]
    task_status: str
    attempts: int
    summary: str
    approved: bool
    decisions: tuple[dict[str, Any], ...]
    artifacts: tuple[str, ...]
    task_context: dict[str, Any]
    project_context: dict[str, Any]
    previous_handoff_ids: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self):
        if self.task_status != 'done':
            raise ValueError('handoffs may only be created for completed tasks')
        if not self.from_agent or self.attempts < 1:
            raise ValueError('handoff requires an agent and a positive attempt number')
        if not all(isinstance(item, str) and item for item in self.to_agents):
            raise ValueError('handoff recipients must be non-empty agent names')
        if not all(isinstance(item, str) for item in self.artifacts):
            raise ValueError('handoff artifacts must be references')

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ('handoff_id', 'tenant_id', 'project_id', 'task_id', 'run_id'):
            data[key] = str(data[key])
        data['to_agents'] = list(self.to_agents)
        data['decisions'] = list(self.decisions)
        data['artifacts'] = list(self.artifacts)
        data['previous_handoff_ids'] = list(self.previous_handoff_ids)
        return data

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'AgentHandoff':
        return cls(
            handoff_id=UUID(value['handoff_id']), tenant_id=UUID(value['tenant_id']),
            project_id=UUID(value['project_id']), task_id=UUID(value['task_id']),
            run_id=UUID(value['run_id']), from_agent=value['from_agent'],
            to_agents=tuple(value.get('to_agents', ())), task_status=value['task_status'],
            attempts=int(value['attempts']), summary=value.get('summary', ''),
            approved=bool(value.get('approved', False)),
            decisions=tuple(value.get('decisions', ())),
            artifacts=tuple(value.get('artifacts', ())),
            task_context=dict(value.get('task_context', {})),
            project_context=dict(value.get('project_context', {})),
            previous_handoff_ids=tuple(value.get('previous_handoff_ids', ())),
            schema_version=int(value.get('schema_version', 1)),
        )
