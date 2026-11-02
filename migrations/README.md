# Migraciones SQL - EXESOFT AI FACTORY

## Estado: F1-002 aplicada; F1-003 lista para revisión

**Fase:** F1-002 - Migraciones SQL versionadas  
**Base:** `exesoft_factory` (PostgreSQL 18.6)  
**Roles:** `exesoft_factory_migrator`, `exesoft_factory_app`

---

## Estructura

```
migrations/
├── README.md                    # Este archivo
├── 000__schema_migrations.sql    # Registro central de migraciones
├── domains/
│   ├── 001__domains.sql           # Tipos de dominio (UUID, Status, etc.)
│   ├── 002__identity.sql          # Dominio Identidad
│   ├── 003__projects.sql          # Dominio Proyectos
│   ├── 004__planning.sql          # Dominio Planificación
│   ├── 005__execution.sql         # Dominio Ejecución
│   ├── 006__memory.sql            # Dominio Memoria
│   ├── 007__events.sql            # Dominio Eventos
│   ├── 008__quality.sql           # Dominio Calidad
│   ├── 009__providers.sql         # Dominio Proveedores
│   ├── 010__artifacts.sql         # Dominio Artefactos
│   ├── 011__indexes.sql           # Índices y optimización
│   ├── 012__integrity_security.sql # Integridad tenant, JSONB y permisos
│   ├── 013__security_defaults_indexes.sql # ACL por defecto e índices audit JSONB
│   └── 014__tenant_edge_guards.sql # Consistencia tenant en relaciones opcionales
```

---

## Principios de Diseño

1. **SQL versionado** sin ORM
2. **Transacciones por migración**
3. **Checksums SHA-256** para detección de alteraciones
4. **Advisory locks** para concurrencia segura
5. **Historial inmutable** de cambios
6. **Permisos mínimos** aplicados por migración
7. **Preparado para multiusuario** con `tenant_id`

---

## Orden de Ejecución

```
000 → 001 → 002 → 003 → 004 → 005 → 006 → 007 → 008 → 009 → 010 → 011 → 012 → 013 → 014
```

---

## Rol de Migraciones

| Rol | Privilegios | Uso |
|-----|-------------|-----|
| `exesoft_factory_migrator` | CONNECT; DDL en `public`; propietario de migraciones | Ejecutar migraciones |
| `exesoft_factory_app` | CONNECT, schema USAGE, SELECT/INSERT/UPDATE/DELETE, secuencias USAGE/SELECT | Operación de la aplicación |

La aplicación no tiene CREATE en la base/esquema, acceso a `schema_migrations` ni a helpers de migración, TRUNCATE, TRIGGER o ownership. Los privilegios por defecto para tablas y secuencias futuras creadas por el migrator están configurados. RLS permanece desactivado.

---

## Historial de Cambios

| Versión | Fecha | Descripción |
|---------|-------|-------------|
| 0.1 | 2026-11-01 | Diseño inicial de migraciones |
| 0.2 | 2026-11-01 | F1-003: integridad tenant, índices JSONB y permisos mínimos en 012–014 |
