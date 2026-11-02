# Pruebas de Repositorios - F2-002

## Resumen

Repository Pattern implementado sobre PostgreSQL con Psycopg 3.

## Archivos Principales

| Archivo | Descripción | Tablas |
|---------|-------------|--------|
| `infrastructure/repositories/base.py` | Clase base con context managers | - |
| `infrastructure/repositories/identity.py` | IdentityRepository | tenants, users, agent_identities |
| `infrastructure/repositories/projects.py` | ProjectsRepository | projects, project_specs, project_snapshots |
| `infrastructure/repositories/planning.py` | PlanningRepository | tasks, task_domains, task_snapshots |
| `infrastructure/repositories/execution.py` | ExecutionRepository | task_runs, orchestrator_state |
| `infrastructure/repositories/memory.py` | MemoryRepository | knowledge, odd_documents, odd_snapshots |
| `infrastructure/repositories/events.py` | EventsRepository | events, audit_log |
| `infrastructure/repositories/quality.py` | QualityRepository | quality_gates, validations, evidence |
| `infrastructure/repositories/providers.py` | ProvidersRepository | providers, models, model_requests |
| `infrastructure/repositories/artifacts.py` | ArtifactsRepository | artifacts, artifact_storage, artifact_dependencies |

## Uso

```python
from infrastructure.repositories import (
    ProjectsRepository,
    PlanningRepository,
    TasksRepository
)
import uuid

tenant_id = uuid.UUID("...")
project_repo = ProjectsRepository(tenant_id=tenant_id)
planning_repo = PlanningRepository(tenant_id=tenant_id)

# Crear proyecto
project = project_repo.create_project(
    tenant_id=tenant_id,
    name="My Project",
    slug="my-project"
)

# Crear tarea
task = planning_repo.create_task(
    tenant_id=tenant_id,
    project_id=project.id,
    title="Implement feature"
)
```

## Características

- **Context Managers**: Uso con `with get_connection() as conn:`
- **Tenant Isolation**: Filtrado automático por tenant_id
- **SQL Parametrizado**: Prevención de SQL injection
- **Manejo de Errores**: Excepciones personalizadas
- **Health Check**: Verificación de conectividad

## Estado

**READY_FOR_REVIEW** - Todos los repositorios implementados.