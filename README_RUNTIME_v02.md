# Exesoft AI Factory — Orchestrator Runtime v0.2

Actualización incremental sobre Core v0.1.1. Mantiene SQLite/FTS5 y no sobrescribe `brain/store.py` ni `engine/orchestrator.py`.

## Instalación

Extraer **el contenido de la carpeta** `runtime_v02` a `C:\AI-Factory`, fusionando `engine/`, `quality/` y `scripts/`. No tocar `freellmapi/`, `.env` ni credenciales.

```powershell
cd C:\AI-Factory
powershell -ExecutionPolicy Bypass -File .\scripts\check-runtime.ps1
```

## Estado y limitaciones

- Scheduler de una tarea lista por paso, dependencias, contador de intentos, trazas por ejecución y estado duradero.
- Reinicio: recupera las tareas `running` como `pending` o `failed` cuando han agotado intentos; NO garantiza ejecución exactamente una vez de efectos externos.
- Dispatcher: requiere adaptadores registrados explícitamente. Sin ellos no ejecuta modelos, CLI ni comandos; bloquea después de intentos limitados.
- QA: solo acepta `Result(success=True, approved=True, evidence=(...))`. Es una puerta de contratos; las evidencias reales aún deberán implementarse con verificadores independientes.
- No hay todavía integración real con FreeLLMAPI ni Claude Code, ni servicio permanente/background, ni sandbox de herramientas.
- `max_steps` acota ciclos; no ejecuta despliegues ni operaciones destructivas.
- El plan básico PM→Arquitecto→Developer→QA→DevOps permanece como esqueleto del Core 0.1.1; ODD dinámico se implementará después.

## Próxima etapa

Adaptador para Claude Code con permisos y directorios aislados, adapter de modelos FreeLLMAPI, políticas de contexto Brain y QA con pruebas verificadas; no se deben conectar hasta cumplir pruebas de seguridad.
