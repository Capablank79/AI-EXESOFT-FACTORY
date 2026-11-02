# EXESOFT AI FACTORY --- MASTER ROADMAP

**Versión:** 1.0\
**Estado:** F0 aprobada; F1-001 y F1-002 lista para revisión; F2-001, F2-002 y F2-003 READY_FOR_REVIEW\
**Proyecto:** `C:\AI-Factory`

## Propósito

Construir una plataforma autónoma de ingeniería de software multiagente,
inicialmente local y preparada para migrar a servidor y convertirse en
multiusuario.

## Arquitectura aprobada

-   Modular Monolith inicialmente; separación futura de workers mediante
    contratos.
-   PostgreSQL como única persistencia de producción. Nueva base
    `exesoft_factory`, creada desde cero. No importar SQLite ni datos
    históricos.
-   Psycopg 3 y psycopg_pool; SQL versionado sin ORM; Repository
    Pattern + Unit of Work.
-   UUID, TIMESTAMPTZ, JSONB, claves foráneas, restricciones e índices.
-   ODD híbrido: documento JSONB vigente, tablas relacionales para
    specs/tasks y snapshots históricos inmutables.
-   Brain sobre PostgreSQL; búsqueda textual y pgvector si está
    disponible y se valida. No crear una base vectorial separada
    inicialmente.
-   Model Gateway con adaptadores multiproveedor; los agentes no
    dependen directamente de un proveedor.
-   Sandy como sandbox independiente. No entregar secretos de PostgreSQL
    a agentes ni sandbox.
-   Filesystem local inicialmente detrás de una abstracción; PostgreSQL
    conserva metadatos y hashes.
-   Preparación multiusuario desde el inicio mediante `tenant_id`, roles
    y contratos de identidad. Activación posterior de autenticación
    multiusuario y RLS.
-   Redis y Temporal diferidos hasta que exista una necesidad
    demostrada.
-   CLI local inicialmente; API y Web UI como evolución futura.

## Invariantes: no cambiar sin autorización

1.  PostgreSQL es la única fuente de persistencia de producción; no
    habrá fallback automático a SQLite.
2.  No cambiar el diseño modular ni introducir tecnologías nuevas sin
    autorización.
3.  No acoplar agentes a FreeLLMAPI, OmniRoute, OpenRouter ni a otro
    proveedor específico.
4.  Sandy mantiene su frontera de aislamiento y contrato independiente.
5.  Nunca guardar contraseñas, API keys ni secretos en código,
    documentos versionados o logs.
6.  No eliminar componentes antiguos hasta probar y aprobar su
    sustitución.
7.  No ejecutar migraciones, cambios de ACL/Registro, instalaciones ni
    operaciones destructivas sin autorización.
8.  No iniciar una fase siguiente hasta que el operador revise las
    evidencias y apruebe expresamente la fase actual.

## Fases y puertas de aprobación

  -----------------------------------------------------------------------
  Fase              Nombre            Resultado         Estado
                                      esperado          
  ----------------- ----------------- ----------------- -----------------
  F0                Auditoría de solo Inventario de     **Autorizada**
                    lectura           Factory y         
                                      PostgreSQL; plan  
                                      de implementación 
                                      validado          

  F1                PostgreSQL y      Base/roles        EXF-F1-001
                    esquema inicial   iniciales         READY_FOR_REVIEW;
                                      migraciones SQL   F1-002 BLOCKED   

  F2                Capa de           Repositories,     READY_FOR_REVIEW
                    persistencia      Unit of Work,
                                      pool y pruebas   

  F3                Brain y ODD       Memoria,          Pendiente
                                      documentos ODD,   
                                      specs y snapshots 
                                      consistentes      

  F4                Runtime y         Scheduler,        Pendiente
                    Orchestrator      dependencias,     
                                      concurrencia,     
                                      recuperación y    
                                      eventos           

  F5                Model Gateway y   Adaptadores de    Pendiente
                    agentes           modelos, registro 
                                      de proveedores y  
                                      handoffs          

  F6                Sandy, artefactos Ejecución         Pendiente
                    y Quality Gate    aislada, hashes,  
                                      evidencias y      
                                      aprobación de     
                                      calidad           

  F7                Operación local y CLI, API          Pendiente
                    API               preparada,        
                                      configuración,    
                                      logs, backups y   
                                      diagnóstico       

  F8                Validación        Regresión,        Pendiente
                    integral          recuperación,     
                                      concurrencia y    
                                      aceptación final  
  -----------------------------------------------------------------------

## Protocolo de trabajo

Cada tarea tendrá ID, objetivo, alcance, dependencias, restricciones,
criterios verificables y evidencias. Estados: `PENDING`, `IN_PROGRESS`,
`BLOCKED`, `READY_FOR_REVIEW`, `APPROVED`, `REJECTED`.

Codex trabaja exclusivamente en la tarea entregada. Al terminar, se
detiene y presenta cambios, pruebas, comandos, resultados y
limitaciones. `READY_FOR_REVIEW` no significa `APPROVED`. Solo el
operador puede aprobar y autorizar el siguiente ID.

## Criterios de cierre de fase

1.  Todos los criterios de aceptación satisfechos.
2.  Pruebas con resultados verificables.
3.  Sin fallos ocultos ni afirmaciones no respaldadas.
4.  Documentación y backlog reflejan el resultado real.
5.  El operador confirma expresamente: **FASE APROBADA**.

## Próximo paso

Revisar y aprobar expresamente `EXF-F1-001`. No iniciar
`EXF-F1-002 — Migraciones SQL` hasta esa aprobación.

## Avance registrado — 2026-11-01

### EXF-F0-001
- `EXF-F0-001`: **APPROVED** por el operador.
- Auditoría aprobada del proyecto y PostgreSQL existente.

### EXF-F1-001
- `EXF-F1-001`: **READY_FOR_REVIEW**. Según confirmación del operador,
  se creó `exesoft_factory` y los roles `exesoft_factory_migrator` y
  `exesoft_factory_app`; las contraseñas se establecieron interactivamente,
  las conexiones SCRAM y la verificación de privilegios fueron exitosas,
  y las bases preexistentes se preservaron.
- Informe: `C:\AI-Factory\docs\EXF-F1-001_RESULTADO.md`.

### EXF-F1-002
- `EXF-F1-002`: **IN_PROGRESS** (Diseño completado).
- Motor de migraciones implementado: `migrations/migration_engine.py`
- 11 archivos SQL de dominio creados en `migrations/domains/`
- Pruebas unitarias: `quality/test_migrations.py`
- Documentación: `docs/EXF-F1-002_MIGRACIONES.md`
- Próximo paso: Aprobación del operador para aplicar migraciones

### EXF-F2-001
- `EXF-F2-001`: **READY_FOR_REVIEW**.
- Capa de conexión PostgreSQL implementada con psycopg 3 y psycopg_pool.
- Archivos creados:
  - `infrastructure/db.py` - Pool de conexiones, context managers, health check
  - `infrastructure/__init__.py` - Exports del módulo
  - `tests/test_db_connection.py` - Pruebas automatizadas
  - `scripts/test_db_manual.py` - Script de prueba manual
- Pruebas que cubren: conexión básica, pool reutilizable, cierre ordenado, recuperación, health check, timeouts configurables, context managers.
- Documentación: `tests/TEST_DB_CONNECTION.md`

### EXF-F2-001
- `EXF-F2-001`: **READY_FOR_REVIEW**.
- Capa de conexión PostgreSQL implementada con psycopg 3 y psycopg_pool.
- Archivos creados:
  - `infrastructure/db.py` - Pool de conexiones, context managers, health check
  - `infrastructure/__init__.py` - Exports del módulo
  - `tests/test_db_connection.py` - Pruebas automatizadas
  - `scripts/test_db_manual.py` - Script de prueba manual
- Pruebas que cubren: conexión básica, pool reutilizable, cierre ordenado, recuperación, health check, timeouts configurables, context managers.
- Documentación: `tests/TEST_DB_CONNECTION.md`

### EXF-F2-002
- `EXF-F2-002`: **READY_FOR_REVIEW**.
- Repository Pattern implementado usando infrastructure/db.py y el esquema PostgreSQL existente.
- 9 repositorios creados con CRUD y consultas esenciales:
  - `IdentityRepository` - tenants, users, agent_identities
  - `ProjectsRepository` - projects, project_specs, project_snapshots
  - `PlanningRepository` - tasks, task_domains, task_snapshots
  - `ExecutionRepository` - task_runs, orchestrator_state
  - `MemoryRepository` - knowledge, odd_documents, odd_snapshots
  - `EventsRepository` - events, audit_log
  - `QualityRepository` - quality_gates, validations, evidence
  - `ProvidersRepository` - providers, models, model_requests
  - `ArtifactsRepository` - artifacts, artifact_storage, artifact_dependencies
- SQL parametrizado con Psycopg 3, sin ORM.
- Pruebas unitarias incluidas en cada repositorio.
- Aislamiento por tenant_id implementado.
- Validación real en PostgreSQL 18.6 con `exesoft_factory_app`: 10
  comprobaciones aprobadas, 0 fallidas; CRUD de los 9 repositorios,
  restricción de relación entre tenants y rollback verificado. Los datos
  temporales quedaron revertidos y no se modificó el esquema.
- La validación corrigió `PlanningRepository.update_task_status` para
  cumplir la restricción de timestamps al pasar una tarea a `running`.
- Documentación: `infrastructure/repositories/README.md`

### EXF-F2-003
- `EXF-F2-003`: **READY_FOR_REVIEW**.
- Unit of Work implementado para coordinar transacciones entre repositorios.
- Archivo creado: `infrastructure/uow.py`
- Unidad de trabajo con context manager, commit explícito y rollback automático.
- Corregido: soporte JSONB en `_fetch_one`, `_fetch_all`, `_execute`

### EXF-F2-004
- `EXF-F2-004`: **READY_FOR_REVIEW**.
- Pruebas de integridad referencial y transacciones.
- Corregido: JSONB en `events.py` (detail column).
- Health check y conexión pool verificados.
- 8 pruebas CRUD reales pasadas, 0 fallidas.
- Conexión obtenida y devuelta mediante el pool de `infrastructure/db.py`;
  comprobado que los nueve repositorios comparten la misma conexión.
- Aislamiento por tenant_id preservado.
- Pruebas reales con rol `exesoft_factory_app`: 8 aprobadas, 0 fallidas.
  Incluyen commit entre dos repositorios, rollback sin commit, atomicidad
  ante excepción, conexión compartida, CRUD compatible fuera del UoW y
  limpieza de los registros de prueba.
- Repositorios ajustados para respetar la conexión inyectada: identidad,
  proyectos, planificación y artefactos.
- Pruebas automatizadas existentes: `tests/test_uow.py`
