# Repositories Package

Repository Pattern sobre PostgreSQL con Psycopg 3 para EXESOFT AI FACTORY.

## Instalación

```python
from infrastructure.db import get_connection
from infrastructure.repositories import ProjectsRepository, PlanningRepository
```

## Repositorios Disponibles

### IdentityRepository
- `tenants`, `users`, `agent_identities`

### ProjectsRepository
- `projects`, `project_specs`, `project_snapshots`

### PlanningRepository
- `tasks`, `task_domains`, `task_snapshots`

### ExecutionRepository
- `task_runs`, `orchestrator_state`

### MemoryRepository
- `knowledge`, `odd_documents`, `odd_snapshots`

### EventsRepository
- `events`, `audit_log`

### QualityRepository
- `quality_gates`, `validations`, `evidence`

### ProvidersRepository
- `providers`, `models`, `model_requests`

### ArtifactsRepository
- `artifacts`, `artifact_storage`, `artifact_dependencies`

## Uso Básico

```python
from infrastructure.repositories import ProjectsRepository
import uuid

# Con tenant isolation
tenant_id = uuid.UUID("...")
repo = ProjectsRepository(tenant_id=tenant_id)

# CRUD
project = repo.create_project(
    tenant_id=tenant_id,
    name="My Project",
    slug="my-project"
)

found = repo.get_project(project.id)
projects = repo.list_projects(tenant_id)
```

## Tenant Isolation

Los repositorios soportan aislamiento por tenant_id. Cuando se proporciona `tenant_id` al constructor, todas las operaciones se filtran automáticamente por ese tenant.