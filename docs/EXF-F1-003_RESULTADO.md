# EXF-F1-003 — Integridad y permisos PostgreSQL

**Estado:** `READY_FOR_REVIEW`  
**Fecha:** 2026-11-01  
**Base:** `exesoft_factory` — PostgreSQL 18.6  
**Migrator:** `exesoft_factory_migrator`  
**App:** `exesoft_factory_app`

## Resultado

Se añadieron y aplicaron tres migraciones nuevas, sin cambiar las migraciones previas:

- `012__integrity_security.sql`: tenant NOT NULL para users, agent identities y projects; claves únicas compuestas y FKs tenant-consistentes para proyectos, tareas, ejecuciones, memoria, eventos, validaciones, artefactos y evidencias; checks para JSONB estructurado y hashes SHA-256; índices de relaciones y JSONB; DML mínimo para app y privilegios por defecto de tablas/secuencias.
- `013__security_defaults_indexes.sql`: checks e índices GIN para ambos campos JSONB de audit_log; revocación global de EXECUTE por defecto en funciones futuras del migrator, necesaria porque un REVOKE por esquema no neutraliza el permiso global implícito de PostgreSQL.
- `014__tenant_edge_guards.sql`: triggers transaccionales que impiden relaciones de tenant incompatibles entre providers/models, models/requests y quality gates/validations; conservan providers globales.

No se modificaron otras bases, membresías ni atributos globales de roles. No se eliminó ninguna tabla o fila; no se activó RLS. Los datos de prueba se insertaron dentro de transacciones revertidas.

## Evidencias de aplicación

`python -m migrations.migration_engine up`: código 0; 1 migración aplicada y 13 omitidas con checksums coincidentes. `status`: código 0; versiones `000`–`014` aplicadas, sin pendientes.

Inspección final de catálogo: 26/26 tablas con PK (25 tablas de dominio más `schema_migrations`), 60 FK, 18 UNIQUE y 55 CHECK; cero constraints no validados y cero índices inválidos. Las 16 columnas JSONB tienen índice GIN; el índice FTS GIN de knowledge permanece presente. No quedaron filas de las pruebas.

## Pruebas reales

- 13/13 inserciones inválidas de tenant, FK, JSONB y hash rechazadas; fixtures revertidos.
- App: login exitoso; SELECT, INSERT, UPDATE y DELETE funcionaron dentro de una transacción revertida.
- App: CREATE persistente/temporal, ALTER, acceso a schema_migrations y ejecución de migration_register rechazados.
- App no tiene privilegios TRUNCATE/TRIGGER, no es propietaria, no es miembro del migrator y no tiene atributos superuser/createdb/createrole/replication/bypassrls.
- Catálogo confirma DML por defecto para tablas futuras, USAGE/SELECT para secuencias futuras, sin EXECUTE público por defecto para funciones nuevas.
- 5/5 casos de guardas tenant probados bajo el rol app: relaciones incompatibles y cambios de tenant rechazados; proveedor global con modelo de tenant permitido.
- Confirmación final: todas las constraints están validadas, índices válidos y probes revertidos.

No se ejecutó la regresión completa de Factory; F1-003 valida el esquema y permisos de PostgreSQL. F2 no se inició.

**Cierre:** EXF-F1-003 queda `READY_FOR_REVIEW`, no `APPROVED`. No iniciar F2 hasta aprobación expresa.
