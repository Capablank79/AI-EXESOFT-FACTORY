# EXF-F1-002 — Resultado de Migraciones SQL

**Estado:** `READY_FOR_REVIEW`  
**Fecha:** 2026-11-01  
**Instancia objetivo:** PostgreSQL 18.6 local, `127.0.0.1:5432`  
**Rol:** `exesoft_factory_migrator`

---

## 📋 Resumen Ejecutivo

Se aplicaron las 12 migraciones `000`–`011` a `exesoft_factory`. `up` terminó con código 0 (1 migración aplicada, 11 omitidas tras comprobar checksums); `status` terminó con código 0 y no mostró migraciones pendientes. La ejecución utilizó `exesoft_factory_migrator`. No se importaron datos SQLite ni se inició F1-003.

---

## 🏗️ Estructura Creada

### Motor de Migraciones
```
migrations/
├── README.md                              # Documentación del motor
├── 000__schema_migrations.sql             # Tabla de registro central
├── migration_engine.py                    # Motor en Python (psycopg3)
├── __init__.py
└── domains/
    ├── 001__domains.sql                   # Tipos de dominio
    ├── 002__identity.sql                  # Identidad/Multiusuario
    ├── 003__projects.sql                  # Proyectos
    ├── 004__planning.sql                  # Planificación
    ├── 005__execution.sql                 # Ejecución
    ├── 006__memory.sql                    # Memoria/Brain
    ├── 007__events.sql                    # Eventos
    ├── 008__quality.sql                   # Quality Gates
    ├── 009__providers.sql                 # Model Gateway
    ├── 010__artifacts.sql                 # Artefactos
    └── 011__indexes.sql                   # Optimización
```

---

## 🔧 Características Implementadas

| Característica | Detalle |
|----------------|---------|
| **Transacciones** | Por migración individual |
| **Advisory Locks** | `pg_advisory_lock(version_hash)` |
| **Checksums** | SHA-256 de contenido SQL |
| **Verificación** | Checksum vs registrado |
| **Dependencies** | Comentarios `-- Dependencia:` |
| **Historial** | Tabla `schema_migrations` inmutable |

---

## 📊 Dominios Definidos

| Dominio | Tablas Principales | Propósito |
|---------|-------------------|-----------|
| **Identidad** | tenants, users, agent_identities | Multiusuario/Multitenant |
| **Proyectos** | projects, project_specs, snapshots | Gestión de proyectos |
| **Planificación** | tasks, task_domains, snapshots | Tareas y dependencias |
| **Ejecución** | task_runs, orchestrator_state | Ejecución por agentes |
| **Memoria** | knowledge, odd_documents, snapshots | Brain/ODD |
| **Eventos** | events, audit_log | Trazabilidad |
| **Calidad** | quality_gates, validations, evidence | Quality Gates |
| **Proveedores** | providers, models, model_requests | Model Gateway |
| **Artefactos** | artifacts, storage, dependencies | Gestión de archivos |

---

## 🧪 Pruebas Implementadas

**Archivo:** `quality/test_migrations.py`

| Prueba | Propósito |
|--------|-----------|
| `test_compute_checksum` | Cálculo SHA-256 |
| `test_extract_dependencies` | Extracción de dependencias |
| `test_load_migrations_order` | Verificación de orden |
| `test_get_lock_key` | Generación consistente de locks |
| `test_dependencies_form_chain` | Cadena de dependencias |
| `test_checksum_detection` | Detección de alteraciones |

---

## Correcciones durante la ejecución

- `migrations/migration_engine.py`: claves advisory convertidas a `BIGINT` con signo y enviadas como parámetros; dependencias verificadas por el nombre literal; rollback añadido ante errores SQL para preservar el error original.
- `migrations/domains/001__domains.sql`: comentarios `//` inválidos cambiados a `--`.
- `migrations/domains/005__execution.sql`: `duration_ms` ya no usa `NOW()` en una expresión generada, pues PostgreSQL requiere una expresión inmutable.
- `migrations/domains/011__indexes.sql`: comparación de `task.id` (`uuid`) con `depends_on` (`text[]`) corregida mediante `task.id::text`.

El estado final confirmó las versiones `000__schema_migrations.sql` y `001__domains.sql` a `011__indexes.sql` aplicadas, sin pendientes. No se ejecutaron pruebas unitarias en esta tarea.

## ⚠️ Alcance y restricciones

| Restricción | Estado |
|-------------|--------|
| DDL limitado a `exesoft_factory` | ✅ Ejecutado con el rol migrator |
| Otras bases / privilegios / roles | ✅ No modificados en esta tarea |
| Importación de datos SQLite | ✅ No realizada |
| Contraseñas y secretos | ✅ No restablecidos ni registrados |
| F1-003 | ✅ No iniciada; permanece bloqueada |

---

## 📋 Registro de Archivos

| Archivo | Tamaño | Propósito |
|---------|--------|-----------|
| migration_engine.py | 10.8 KB | Motor de migraciones |
| 001__domains.sql | 3.0 KB | Tipos de dominio |
| 002__identity.sql | 2.7 KB | Identidad |
| 003__projects.sql | 2.5 KB | Proyectos |
| 004__planning.sql | 3.1 KB | Planificación |
| 005__execution.sql | 2.1 KB | Ejecución |
| 006__memory.sql | 3.5 KB | Memoria |
| 007__events.sql | 2.0 KB | Eventos |
| 008__quality.sql | 3.1 KB | Calidad |
| 009__providers.sql | 3.5 KB | Proveedores |
| 010__artifacts.sql | 2.6 KB | Artefactos |
| 011__indexes.sql | 5.4 KB | Optimización |
| test_migrations.py | 5.5 KB | Pruebas unitarias |
| EXF-F1-002_MIGRACIONES.md | 5.7 KB | Documentación |

---

## 🎯 Próximos Pasos

1. Revisión y aprobación del operador para EXF-F1-002.
2. No iniciar EXF-F1-003 hasta recibir aprobación expresa.

---

## 📈 Estado de Progreso

| Tarea | Estado |
|-------|--------|
| EXF-F0-001 | ✅ APPROVED |
| EXF-F1-001 | ✅ READY_FOR_REVIEW |
| EXF-F1-002 | ✅ READY_FOR_REVIEW (migraciones aplicadas; `status` sin pendientes) |
| EXF-F1-003 | 🔴 BLOCKED |
| F2-F8 | 🔴 PENDIENTE |

---

## Evidencia de comandos

`python -m migrations.migration_engine up`:

```text
Migraciones aplicadas: 1
Migraciones omitidas: 11
EXIT_CODE=0
```

`python -m migrations.migration_engine status`: versiones `000`–`011` enumeradas como aplicadas; sección de pendientes vacía; `EXIT_CODE=0`.

`READY_FOR_REVIEW` no equivale a `APPROVED`. EXF-F1-003 continúa `BLOCKED`.

## 📞 Soporte

Para preguntas o ajustes, contactar al equipo de arquitectura del proyecto.
