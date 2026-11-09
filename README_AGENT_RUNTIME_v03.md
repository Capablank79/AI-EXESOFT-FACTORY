# Exesoft AI Factory — Agent Runtime v0.3

## Instalación incremental

Extraer el contenido interno de `agent_v03/` en `C:\AI-Factory`. Archivos nuevos únicamente: `engine/agents.py`, `engine/agent_runtime.py`, `quality/test_agents.py`, `scripts/check-agents.ps1` y este documento. No sobreescribir `brain/store.py`, `engine/runtime.py` ni `freellmapi/`.

Validar:
```powershell
cd C:\AI-Factory
powershell -ExecutionPolicy Bypass -File .\scripts\check-agents.ps1
```

## Seguridad y límites

- **No existe ejecución autónoma de cambios de código en v0.3.** Claude Code se inicia con `--permission-mode plan` y bloqueo explícito de `Bash`, `Edit`, `Write`, `NotebookEdit`; solo produce análisis.
- El adaptador FreeLLMAPI usa únicamente el endpoint local `127.0.0.1:3001/v1/chat/completions` y no ejecuta herramientas.
- Las credenciales se leen solo desde variables de entorno (`EXESOFT_ROUTER_KEY`, `ANTHROPIC_AUTH_TOKEN`); nunca se guardan en SQLite.
- Debe estar ejecutándose FreeLLMAPI. `ANTHROPIC_BASE_URL` debe valer `http://127.0.0.1:3001` para Claude Code.
- AgentRuntime usa Brain PostgreSQL y busca hasta cinco términos con aislamiento por tenant/proyecto; no se inyecta el historial completo.
- QA continúa siendo una compuerta preliminar en Runtime v0.2: **una respuesta de IA no constituye evidencia QA auténtica**. El adaptador actual no eleva `approved=True`, así que las tareas QA se bloquearán correctamente hasta disponer de verificación automatizada real.
- Se aplican límites de turnos, tiempo de ejecución y reintentos. No se garantizan límites absolutos de tokens o gastos del proveedor.
- Las pruebas usan dobles de ejecución; no implican conectividad real. Una prueba real debe ser explícitamente autorizada y supervisada.

## Próximo paso

Verificar entorno en Windows y ejecutar una tarea de lectura real supervisada, después incorporar ejecución restringida con Git y QA basado en pruebas reproducibles. No ejecutar una canalización completa con `run()` como sustituto de QA.
