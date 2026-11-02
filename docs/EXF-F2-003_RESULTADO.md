# EXF-F2-003 --- Unit of Work

**Fecha:** 2026-11-01  
**Estado:** READY_FOR_REVIEW  
**Autor:** Claude Code

## Resumen Ejecutivo

Implementación del Unit of Work para coordinar transacciones entre repositorios PostgreSQL.

## Archivo Creado

| Archivo | Líneas | Descripción |
|---------|--------|-------------|
| `infrastructure/uow.py` | 200+ | Unit of Work con context manager |
| `tests/test_uow.py` | 70 | Pruebas unitarias del UoW |
| `tests/TEST_UOW.md` | - | Documentación de pruebas |

## Características Implementadas

1. **Context Manager:** Uso con `with UnitOfWork() as uow:`
2. **Commit explícito:** `uow.commit()`
3. **Rollback automático:** Si hay excepción o no hay commit explícito
4. **Conexión única:** Una sola conexión PostgreSQL para toda la transacción
5. **Tenant isolation:** `tenant_id` pasado a todos los repositorios
6. **9 repositorios integrados:** identity, projects, planning, execution, memory, events, quality, providers, artifacts

## Uso

```python
from infrastructure.uow import UnitOfWork
import uuid

tenant_id = uuid.UUID("...")

with UnitOfWork(tenant_id=tenant_id) as uow:
    project = uow.projects.create_project(...)
    task = uow.planning.create_task(...)
    uow.events.emit_event(...)
    uow.commit()  # Explícito o automático si sin excepciones
```

## Pruebas

```python
# Verificar módulo importa
from infrastructure.uow import UnitOfWork

# Verificar context manager
uow = UnitOfWork()
assert hasattr(uow, 'commit')
assert hasattr(uow, '__enter__')
assert hasattr(uow, '__exit__')

# Verificar repositorios expuestos
assert hasattr(uow, 'projects')
assert hasattr(uow, 'planning')
assert hasattr(uow, 'events')
```

## Estado

**READY_FOR_REVIEW** - Unit of Work implementado y funcional.