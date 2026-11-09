# EXESOFT AI FACTORY --- TASK BACKLOG

**Documento rector:** `MASTER_ROADMAP.md`\
**Estado vigente (2026-11-06):** F0–F7 APPROVED; F8-001, F8-002 y F8-003 APPROVED; F8-004 IN_PROGRESS.

El estado vigente y la tabla final son canónicos. Los campos de estado en las
descripciones heredadas más abajo son capturas históricas y no reflejan las
aprobaciones posteriores del operador.

## Reglas

-   Una tarea activa a la vez.
-   No iniciar dependencias antes de aprobar la anterior.
-   No ampliar alcance ni introducir tecnologías sin autorización.
-   Codex se detiene al terminar cada tarea y entrega evidencias.
-   `READY_FOR_REVIEW` no significa `APPROVED`.
-   Solo el operador aprueba el cierre y autoriza el siguiente ID.

## F0 --- Auditoría de solo lectura

### EXF-F0-001 --- Auditoría del proyecto y PostgreSQL

**Estado:** APPROVED\
**Dependencias:** Ninguna\
**Objetivo:** inspeccionar `C:\AI-Factory` y PostgreSQL existente sin
modificar nada.

**Incluye:** inventario de estructura y módulos; mapa de dependencias
SQLite; estado de pruebas; versión, servicio, puerto, extensiones y
autenticación PostgreSQL; disponibilidad de pgvector sin instalarlo;
propuesta de esquema, roles, migraciones y orden de refactor; riesgos y
decisiones pendientes.

**Prohibido:** crear bases, roles, esquemas o tablas; modificar
archivos; instalar paquetes/extensiones; ejecutar migraciones; cambiar
ACL/Registro; ejecutar inferencias o pruebas Sandy con efectos
laterales.

**Aceptación:** informe reproducible, comandos de solo lectura usados,
hallazgos respaldados por rutas/salidas, riesgos y plan F1. Sin
modificaciones.

**Salida:** `READY_FOR_REVIEW`; detenerse y esperar aprobación.

## F1 --- PostgreSQL y esquema inicial

### EXF-F1-001 --- Preparación PostgreSQL

**Estado:** READY_FOR_REVIEW\
**Dependencia:** EXF-F0-001 APPROVED\
**Evidencia:** `C:\AI-Factory\docs\EXF-F1-001_RESULTADO.md`\
**Resultado informado por el operador:** base `exesoft_factory` y roles
`exesoft_factory_migrator` / `exesoft_factory_app` creados; contraseñas
configuradas interactivamente; conexiones SCRAM exitosas; privilegios
correctos; bases preexistentes preservadas. No se incluyen secretos.
La confirmación y sus límites de evidencia constan en el informe.

**Alcance cerrado:** sin esquemas, tablas, índices, extensiones ni
migraciones. No avanzar a F1-002 hasta aprobación expresa.

-   **EXF-F1-001:** crear `exesoft_factory` y los dos roles separados;
    no tocar otras bases ni cuentas. **READY_FOR_REVIEW**.
-   **EXF-F1-002 --- Migraciones SQL:** esquema versionado, checksums y
    tabla de control. Dominios: identidad, proyectos, planificación,
    ejecución, memoria, eventos, calidad, proveedores y artefactos.
-   **EXF-F1-003 --- Integridad y seguridad:** PK/FK, restricciones,
    índices, permisos mínimos y preparación `tenant_id`. RLS queda para
    la fase multiusuario autorizada. **Puerta F1:** conexión verificada,
    esquema reproducible desde cero, roles probados y rollback
    documentado.

## F2 --- Capa de persistencia

-   **EXF-F2-001:** Psycopg 3 / psycopg_pool, configuración por entorno,
    secretos externos y cierre ordenado.
-   **EXF-F2-002:** repositorios por dominio; prohibir SQL directo en
    servicios de negocio.
-   **EXF-F2-003:** Unit of Work, commit/rollback y pruebas
    transaccionales.
-   **EXF-F2-004:** CRUD, integridad referencial, concurrencia, rollback
    y recuperación de conexiones. **Puerta F2:** servicios usan
    contratos de persistencia y pruebas aprobadas.

## F3 --- Brain y ODD

-   **EXF-F3-001:** Brain sobre PostgreSQL: memoria de trabajo,
    episódica y semántica. **READY_FOR_REVIEW**
    - Evidencia: `brain/postgres_store.py`, `docs/EXF-F3-001_RESULTADO.md`
    - Pruebas: `tests/test_brain_f3001_simple.py`, `scripts/run_tests_f3001.ps1`

-   **EXF-F3-002:** ODD híbrido: JSONB vigente, specs/tasks relacionales
    y snapshots inmutables. **READY_FOR_REVIEW**
    - Evidencia: `infrastructure/repositories/odd_hybrid.py`, `docs/EXF-F3-002_RESULTADO.md`
    - Pruebas: `tests/test_odd_hybrid_f3002.py`, `scripts/run_tests_f3002.ps1`
    - 19 pruebas funcionales pasadas

-   **EXF-F3-003:** FTS; pgvector solo si está disponible, validado y
    aprobado. **BLOCKED**
-   **EXF-F3-004:** idempotencia, versiones, estados y referencias a
    Runtime. **BLOCKED** **Puerta F3:** carga/actualización repetible, historial
    íntegro y sincronización demostrada.

## F4 --- Runtime y Orchestrator

-   **EXF-F4-001:** eliminar persistencia SQLite de rutas activas.
-   **EXF-F4-002:** scheduler y reclamo con `FOR UPDATE SKIP LOCKED`,
    lease/timeout y prevención de doble ejecución.
-   **EXF-F4-003:** recuperación, estados huérfanos, reintentos e
    idempotencia.
-   **EXF-F4-004:** eventos persistentes y trazabilidad. **Puerta F4:**
    concurrencia de proyectos/tareas sin corrupción ni duplicación
    indebida.

## F5 --- Model Gateway y agentes

-   **EXF-F5-001:** contrato universal de modelos.
-   **EXF-F5-002:** catálogo de proveedores/modelos, capacidades,
    límites y disponibilidad.
-   **EXF-F5-003:** selección/fallback explícito según capacidad, coste
    y timeout.
-   **EXF-F5-004:** handoffs trazables entre PM, Architect, UX/UI,
    Developer, DevOps y QA. **Puerta F5:** cambiar proveedor sin
    modificar agentes; handoffs verificables.

## F6 --- Sandy, artefactos y Quality Gate

-   **EXF-F6-001:** interfaz estable de Sandbox Engine; no entregar
    secretos de DB a Sandy.
-   **EXF-F6-002:** workspace por proyecto/ejecución; almacenamiento
    físico y metadatos/hash en PostgreSQL.
-   **EXF-F6-003:** rechazar pruebas fallidas, evidencia incompleta o
    hashes inválidos.
-   **EXF-F6-004:** integración Sandy con efectos ACL/AppContainer solo
    con autorización específica. **Puerta F6:** aislamiento comprobado y
    Quality Gate sin bypass.

## F7 --- Operación local y API

-   **EXF-F7-001:** CLI y configuración segura.
-   **EXF-F7-002:** contratos API desacoplados; no publicar servicio sin
    autorización.
-   **EXF-F7-003:** backups PostgreSQL + artefactos y prueba de
    restauración.
-   **EXF-F7-004:** logs estructurados, métricas y diagnóstico. **Puerta
    F7:** operación local reproducible y restauración demostrada.

## F8 --- Validación integral

-   **EXF-F8-001:** regresión completa.
-   **EXF-F8-002:** prueba multi-proyecto, aislamiento y concurrencia.
-   **EXF-F8-003:** recuperación tras reinicio e interrupciones.
-   **EXF-F8-004:** auditoría final de arquitectura, seguridad y
    documentación. **Puerta F8:** informe final respaldado por
    evidencias; no declarar producción lista con fallos.

## Estado vigente y aprobaciones

| Alcance | Estado vigente |
| --- | --- |
| F0–F7 (todas las tareas) | APPROVED |
| EXF-F8-001 | APPROVED |
| EXF-F8-002 | APPROVED |
| EXF-F8-003 | APPROVED |
| EXF-F8-004 | IN_PROGRESS |

Las aprobaciones F0–F8-003 fueron confirmadas por el operador. EXF-F8-004
permanece activa hasta completar la auditoría y su remediación; no iniciar
trabajo posterior al roadmap.