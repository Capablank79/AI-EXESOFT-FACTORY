# EXESOFT AI FACTORY --- TASK BACKLOG

**Documento rector:** `MASTER_ROADMAP.md`\
**Estado actual:** F0 aprobada; F1-001, F1-002, F2-001 y F2-002 READY_FOR_REVIEW;
F3-F8 bloqueadas.

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
    episódica y semántica.
-   **EXF-F3-002:** ODD híbrido: JSONB vigente, specs/tasks relacionales
    y snapshots inmutables.
-   **EXF-F3-003:** FTS; pgvector solo si está disponible, validado y
    aprobado.
-   **EXF-F3-004:** idempotencia, versiones, estados y referencias a
    Runtime. **Puerta F3:** carga/actualización repetible, historial
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

## Registro de avance

  -----------------------------------------------------------------------
  ID                      Estado                  Evidencia / decisión
  ----------------------- ----------------------- -----------------------
  EXF-F0-001              APPROVED                 Auditoría aprobada
                                                  por operador

  EXF-F1-001              READY_FOR_REVIEW         Informe en docs/
                                                  EXF-F1-001_RESULTADO.md

  EXF-F1-002              IN_PROGRESS              migrations/ (12 archivos SQL)
                                                  docs/EXF-F1-002_MIGRACIONES.md
                                                  quality/test_migrations.py

  EXF-F2-001              READY_FOR_REVIEW         infrastructure/db.py
                                                  tests/test_db_connection.py
                                                  scripts/test_db_manual.py
                                                  tests/TEST_DB_CONNECTION.md

  EXF-F2-002              READY_FOR_REVIEW         infrastructure/repositories/
                                                  (9 repositorios completados)
                                                  Prueba real APP: 10 aprobadas,
                                                  0 fallidas; CRUD y rollback

  EXF-F2-003              READY_FOR_REVIEW         infrastructure/uow.py
                                                  tests/test_uow.py
                                                  Pruebas reales APP:
                                                  8 aprobadas, 0 fallidas

  EXF-F2-004              READY_FOR_REVIEW         infrastructure/repositories/
                                                  scripts/test_f2004_pending.py
                                                  scripts/run_tests_f2004.ps1
                                                  8 pruebas base + 6 pendientes

  EXF-F1-003 a EXF-F8-004 BLOCKED                 Requieren revisión y
                                                  autorización secuencial
  -----------------------------------------------------------------------
