# PLAN MASTER

## VISIÓN GENERAL
EXESOFT AI FACTORY es una fábrica de software autónoma con persistencia basada en SQLite Brain, orquestador de estados, ODD y agentes especializados.

## FLUJO DE 6 AGENTES
```
Solicitud → Orchestrator::create_project() → plan_skeleton() → run_controlled()
          → pm → architect → ux → developer → qa → devops
```

## ESTADO ACTUAL (2026-10-31 23:00 UTC)
| Componente | Estado | Pruebas |
|------------|--------|---------|
| Brain SQLite + FTS5 | ✅ Operativo | TestKnowledgeFTS |
| Orchestrator (6 agentes) | ✅ Operativo | test_core.py (3 pruebas) |
| ODD Engine | ✅ Operativo | Integrado |
| Agent Runtime | ✅ Operativo | test_agents.py (6 pruebas) |
| Autonomous Engine | ✅ Operativo | test_autonomous.py (9 pruebas) |
| Quality Engine | ✅ Operativo | test_quality.py (9 pruebas) |
| Execution Sandbox | ✅ Operativo | test_execution_sandbox.py (8 pruebas) |
| Windows Sandbox Adapter | ✅ Configurado | test_sandbox_adapter.py (4 pruebas) |
| **TOTAL** | **✅ 58/58 pruebas pasadas** | |

### Evidencias de ejecución:
```
58 passed in 15.04s
```

## NUEVO: SANDY SANDBOX v0.9996

### Estado
- **SandySandboxAdapter**: ✅ Implementado
- **Ejecutable**: `C:\Users\{USER}\Sandy-Evaluation\sandy.exe`
- **Sintaxis**: `sandy.exe -c <config.toml> -x <executable> [args...]`

### Capacidades
- ✅ AppContainer sin ventanas virtuales
- ✅ Sin Docker/WSL2/Hyper-V
- ✅ Escritura controlada por ACLs
- ✅ Red bloqueada por defecto
- ✅ Timeout y límites de recursos
- ✅ SHA-256 de salidas

### Archivos modificados
1. `engine/sandy_adapter.py` - Nuevo adaptador principal
2. `engine/agent_runtime.py` - Import añadido
3. `factory.py` - Lógica de selección de adaptador

### Archivos creados
- `scripts/validate-sandy.ps1` - Script de validación completa
- `quality/test_sandy_adapter.py` - Pruebas unitarias

### Archivos eliminados
- `quality/test_trae_adapter.py` - TRAE no funcional

## CAPACIDADES VERIFICADAS
1. ✅ Creación de proyectos con ID único
2. ✅ Planificación ODD con Specs y Tasks
3. ✅ Orquestación con dependencias
4. ✅ Recuperación de interrupciones
5. ✅ QA Gate (bloquea sin evidencia)
6. ✅ Evidencia SHA256
7. ✅ Windows Sandbox detectado sin privilegios

## BLOQUEOS CONCRETOS

| # | Bloqueos | Estado |
|----|----------|--------|
| 1 | Windows Sandbox requiere interacción humana | ⚠️ Manual requerido |
| 2 | TRAE Sandbox SDK no funcional (sdk_load_failed) | ❌ Eliminado del flujo |
| 3 | Sandy: Validación de aislamiento pendiente | ⏳ Por validar |

### Estado de FreeLLMAPI
- ✅ Servidor activo en http://127.0.0.1:3001
- ✅ EXESOFT_ROUTER_KEY configurada

### Estado de Claude Code
- ✅ Auto mode habilitado
- ✅ Modo solo-planificación (read-only)

### Estado de Windows Sandbox
- ✅ Ejecutable detectado en System32
- ✅ Genera archivos .wsb válidos
- ⚠️ Requiere UI interactiva para ejecución

## COMANDO ÚNICO PARA INICIAR LA FÁBRICA

```powershell
cd C:/AI-Factory

# Verificar entorno
.\scripts\check-autonomous.ps1

# Validar Sandy Sandbox (recomendado)
.\scripts\validate-sandy.ps1

# Crear y ejecutar proyecto
python -c "from factory import run_real_task; result = run_real_task('Demo', 'Prueba automatizada'); print(result)"
```

## COMANDO DE INICIO OPERATIVO PARA PRODUCCIÓN

```powershell
# Con Sandy (backend preferido)
python -c "from factory import run_real_task; result = run_real_task('MiProyecto', 'Descripción'); print(result)"

# Con Windows Sandbox (cuando esté disponible)
# Ejecutar manualmente: WindowsSandbox.exe workspas/{pid}/sandbox.wsb
```

## PRÓXIMOS PASOS

| # | Acción | Estado |
|---|--------|--------|
| 1 | ✅ Verificar 58/58 tests pasan | Completado |
| 2 | ✅ Configurar EXESOFT_ROUTER_KEY | Completado |
| 3 | ✅ Integrar Sandy como backend | Implementado |
| 4 | ⏳ Validar aislamiento real con Sandy | **PENDIENTE** |

---

**ESTADO: NÚCLEO VALIDADO; EJECUCIÓN SANDY PENDIENTE**

## Revisión Sandy v0.9996 — 2026-11-01

- La configuración TOML pasa `sandy --dry-run`; esta comprobación no modifica ACL.
- La ejecución AppContainer **no está validada**: Sandy devuelve `129 SetupError` con `GRANT_TRACKING: create grants key FAILED`.
- Evidencia local: `HKCU\Software` no concede escritura a la identidad del usuario; Sandy no puede crear `HKCU\Software\Sandy\Grants\<instance>`.
- `sandy --status --json` reportó cero instancias stale y cero tareas huérfanas antes y después de los intentos.
- La validación real requiere resolver el acceso de registro del usuario y volver a ejecutar `scripts\validate-sandy.ps1`. No se modificó el ACL del registro.
- La regresión unitaria del núcleo pasó; la integración real de AppContainer sigue pendiente por el bloqueo anterior.
- Inicio: `python factory.py run "Nombre" "Solicitud"`; reanudación: `python factory.py resume <project_id>`.
- Mientras Sandy devuelva `SetupError 129`, `run` reporta preflight bloqueado y no consulta FreeLLMAPI ni genera/modifica código; conserva el proyecto planificado para reanudación.

La Factory incorpora **Sandy Sandbox como backend fail-closed**. La validación completa del aislamiento debe ejecutarse mediante `scripts\validate-sandy.ps1` para confirmar que:
- Python ejecuta correctamente en AppContainer
- Escritura está restringida al workspace
- Red está bloqueada
- Timeout funciona
- Estado residual se limpia

**TRAE Sandbox ha sido eliminado del flujo** por fallas de SDK sin necesidad de modificar la aplicación TRAE.

## COMANDO FINAL DE INICIO

```powershell
cd C:/AI-Factory
.\scripts\check-autonomous.ps1
# o directamente:
.\scripts\validate-sandy.ps1
```
