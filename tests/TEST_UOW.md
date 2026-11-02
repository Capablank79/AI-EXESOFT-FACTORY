# Pruebas Unit of Work - F2-003

## Resumen

Unit of Work implementado para coordinar transacciones entre repositorios PostgreSQL.

## Archivos

| Archivo | Descripción |
|---------|-------------|
| `infrastructure/uow.py` | Implementación del Unit of Work |

## Uso

```python
from infrastructure.uow import UnitOfWork
import uuid

tenant_id = uuid.UUID("...")

# Uso como context manager
with UnitOfWork(tenant_id=tenant_id) as uow:
    # Múltiples operaciones en una transacción
    project = uow.projects.create_project(
        tenant_id=tenant_id,
        name="My Project",
        slug="my-project"
    )
    
    task = uow.planning.create_task(
        tenant_id=tenant_id,
        project_id=project.id,
        title="Implement feature"
    )
    
    uow.events.emit_event(
        type="project.created",
        detail={"project_id": str(project.id)}
    )
    
    uow.commit()  # O se autocommitea al salir del contexto sin excepciones

# Si hay una excepción, se hace rollback automáticamente
```

## Características

- **Context Manager**: Manejo automático de transacciones
- **Connectión única**: Una sola conexión para toda la transacción
- **Rollback automático**: Si hay excepción
- **Commit explícito**: O automático al salir del contexto
- **Tenant isolation**: Aislamiento por tenant_id

## Estado

**READY_FOR_REVIEW** - Unit of Work implementado y funcional.