# EXF-F1-002 — Migraciones SQL Versionadas

**Estado:** IN_PROGRESS  
**Fecha:** 2026-11-01  
**Base:** `exesoft_factory` (PostgreSQL 18.6)

---

## 📋 Resumen Ejecutivo

Se ha implementado completamente el motor de migraciones SQL y las estructuras de dominio necesarias para la migración desde SQLite a PostgreSQL.

---

## 🏗️ Estructura Implementada

```
migrations/
├── README.md
├── 000__schema_migrations.sql     # Registro central
├── migration_engine.py            # Motor de migraciones
├── __init__.py
└── domains/
    ├── 001__domains.sql           # Tipos de dominio (UUID, Status)
    ├── 002__identity.sql            # Dominio Identidad
    ├── 003__projects.sql            # Dominio Proyectos
    ├── 004__planning.sql            # Dominio Planificación
    ├── 005__execution.sql           # Dominio Ejecución
    ├── 006__memory.sql              # Dominio Memoria
    ├── 007__events.sql              # Dominio Eventos
    ├── 008__quality.sql             # Dominio Calidad
    ├── 009__providers.sql           # Dominio Proveedores
    ├── 010__artifacts.sql           # Dominio Artefactos
    └── 011__indexes.sql             # Índices y optimización
```

---

## 🔧 Motor de Migraciones

### Características

| Característica | Implementación |
|----------------|----------------|
| Transacciones por migración | ✅ Usa `conn.commit()` por migración |
| Advisory locks | ✅ `pg_advisory_lock()` por versión |
| Checksums SHA-256 | ✅ Detecta alteraciones |
| Verificación de dependencias | ✅ Comentarios `-- Dependencia:` |
| Historial inmutable | ✅ Tabla `schema_migrations` |
| Rol de migraciones | ✅ `exesoft_factory_migrator` |

### Uso

```bash
# Aplicar migraciones
& 'C:\AI-Factory\.venv\Scripts\python.exe' -m migrations.migration_engine up

# Ver estado
& 'C:\AI-Factory\.venv\Scripts\python.exe' -m migrations.migration_engine status
```

---

## 📊 Dominios Definidos

| ID | Dominio | Tablas Principales | Propósito |
|----|---------|-------------------|-----------|
| 001 | Domains | domain_uuid, domain_status | Tipos reutilizables |
| 002 | Identity | tenants, users, agent_identities | Multiusuario/empresa |
| 003 | Projects | projects, project_specs, snapshots | Gestión de proyectos |
| 004 | Planning | tasks, task_domains, snapshots | Planificación de trabajo |
| 005 | Execution | task_runs, orchestrator_state | Ejecución por agentes |
| 006 | Memory | knowledge, odd_documents, snapshots | Memoria del sistema |
| 007 | Events | events, audit_log | Registro de hechos |
| 008 | Quality | quality_gates, validations, evidence | Quality Gates |
| 009 | Providers | providers, models, requests | Model Gateway |
| 010 | Artifacts | artifacts, storage, dependencies | Gestión de archivos |
| 011 | Indexes | vistas, funciones, constraints | Optimización |

---

## 🔐 Seguridad y Permisos

### Roles Definidos

| Rol | Privilegios | Uso |
|-----|-------------|-----|
| `exesoft_factory_migrator` | CREATE, CONNECT | Ejecutar migraciones |
| `exesoft_factory_app` | CONNECT, SELECT, INSERT, UPDATE | Aplicación |

### Restricciones

- ✅ No se modifican bases preexistentes
- ✅ No se importan datos SQLite
- ✅ No se guardan secretos
- ✅ Transacciones por migración
- ✅ Rollbacks documentados

---

## 🧪 Pruebas Implementadas

**Archivo:** `quality/test_migrations.py`

| Prueba | Propósito |
|--------|-----------|
| `test_compute_checksum` | Verifica cálculo SHA-256 |
| `test_extract_dependencies` | Extrae dependencias SQL |
| `test_load_migrations_order` | Verifica orden numérico |
| `test_get_lock_key` | Generación consistente de locks |
| `test_dependencies_form_chain` | Cadena de dependencias |
| `test_checksum_detection` | Detección de alteraciones |

---

## 📝 Documentación Adjunta

1. **README.md** - Estructura y uso del motor
2. **migration_engine.py** - Código fuente del motor
3. **test_migrations.py** - Pruebas unitarias
4. **Cada archivo .sql** - Con checksum y documentación

---

## ⏭️ Próximos Pasos

1. ✅ **Diseño completado** - Todos los dominios definidos
2. ✅ **Archivos creados** - 12 archivos SQL + motor + pruebas
3. ✅ **Documentación escrita** - Este informe

**Pendiente:**
- [ ] **APPROVAL PENDIENTE** - El operador debe autorizar
- [ ] **PLAN DE EJECUCIÓN** - Definir cuándo aplicar migraciones
- [ ] **PRUEBAS DE INTEGRACIÓN** - Con base `exesoft_factory`

---

## 📋 Registro de Cambios

| Archivo | Acción |
|---------|--------|
| migrations/README.md | ✅ Creado |
| migrations/000__schema_migrations.sql | ✅ Creado |
| migrations/domains/001__domains.sql | ✅ Creado |
| migrations/domains/002__identity.sql | ✅ Creado |
| migrations/domains/003__projects.sql | ✅ Creado |
| migrations/domains/004__planning.sql | ✅ Creado |
| migrations/domains/005__execution.sql | ✅ Creado |
| migrations/domains/006__memory.sql | ✅ Creado |
| migrations/domains/007__events.sql | ✅ Creado |
| migrations/domains/008__quality.sql | ✅ Creado |
| migrations/domains/009__providers.sql | ✅ Creado |
| migrations/domains/010__artifacts.sql | ✅ Creado |
| migrations/domains/011__indexes.sql | ✅ Creado |
| migrations/migration_engine.py | ✅ Creado |
| migrations/__init__.py | ✅ Creado |
| quality/test_migrations.py | ✅ Creado |

---

## ⚠️ Limitaciones Conocidas

1. **No hay datos migrados** - Solo estructura, sin importar datos de SQLite
2. **F2 no implementado** - Repository Pattern/UoW vendrá en F2-001
3. **No hay datos de prueba** - Pruebas unitarias pero sin datos reales

---

## 📞 Soporte

Para preguntas o ajustes, contactar al equipo de arquitectura.
