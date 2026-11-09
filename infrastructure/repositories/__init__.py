"""Repositorios de datos para EXESOFT AI FACTORY.

Repository Pattern sobre PostgreSQL con Psycopg 3.
SQL parametrizado sin ORM.

Dominios implementados:
- identity: tenants, users, agent_identities
- projects: projects, project_specs, project_snapshots
- planning: tasks, task_domains, task_snapshots
- execution: task_runs, orchestrator_state
- memory: knowledge, odd_documents, odd_snapshots
- events: events, audit_log
- quality: quality_gates, validations, evidence
- providers: providers, models, model_requests
- artifacts: artifacts, artifact_storage, artifact_dependencies
- odd_hybrid: documento ODD vigente, specs/tasks relacionales, snapshots históricos

Autor: Claude Code - EXESOFT AI FACTORY
"""

from .base import Repository, RepositoryError, RecordNotFoundError, PaginatedResult
from .identity import IdentityRepository, Tenant, User, AgentIdentity
from .projects import ProjectsRepository, Project, ProjectSpec, ProjectSnapshot
from .planning import PlanningRepository, Task, TaskDomain, TaskSnapshot
from .execution import ExecutionRepository, TaskRun, OrchestratorState
from .memory import MemoryRepository, Knowledge, OddDocument, OddSnapshot
from .events import EventsRepository, Event, AuditLog
from .quality import QualityRepository, QualityGate, Validation, Evidence
from .providers import ProvidersRepository, Provider, Model, ModelRequest
from .artifacts import ArtifactsRepository, Artifact, ArtifactStorage, ArtifactDependency
from .odd_hybrid import OddHybridRepository, OddDocument, OddSnapshot, OddTaskLink

__all__ = [
    # Base
    "Repository",
    "RepositoryError",
    "RecordNotFoundError",
    "PaginatedResult",
    # Identity
    "IdentityRepository",
    "Tenant",
    "User",
    "AgentIdentity",
    # Projects
    "ProjectsRepository",
    "Project",
    "ProjectSpec",
    "ProjectSnapshot",
    # Planning
    "PlanningRepository",
    "Task",
    "TaskDomain",
    "TaskSnapshot",
    # Execution
    "ExecutionRepository",
    "TaskRun",
    "OrchestratorState",
    # Memory
    "MemoryRepository",
    "Knowledge",
    "OddDocument",
    "OddSnapshot",
    # Events
    "EventsRepository",
    "Event",
    "AuditLog",
    # Quality
    "QualityRepository",
    "QualityGate",
    "Validation",
    "Evidence",
    # Providers
    "ProvidersRepository",
    "Provider",
    "Model",
    "ModelRequest",
    # Artifacts
    "ArtifactsRepository",
    "Artifact",
    "ArtifactStorage",
    "ArtifactDependency",
    # ODD Hybrid
    "OddHybridRepository",
    "OddDocument",
    "OddSnapshot",
    "OddTaskLink",
]