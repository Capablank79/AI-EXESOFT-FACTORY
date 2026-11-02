# EXESOFT AI FACTORY — Execution Sandbox v0.6

**Estado:** Preparación segura para ejecución aislada; **no** es un sandbox de sistema operativo.

## Funciones incorporadas

- `engine/execution_sandbox.py`: políticas *fail-closed*: ninguna instrucción, prueba ni programa generado por agentes recibe autorización de ejecución en el host.
- Git Worktree separado por tarea a partir de un repositorio con commit inicial, sin modificar la copia de trabajo principal. Controles de identificador, rutas, existencia y configuración de Git.
- `quality/test_execution_sandbox.py`: 8 pruebas nuevas, incluido aislamiento de cambios entre worktree y repositorio.
- `scripts/check-sandbox.ps1`: vuelve a ejecutar **toda** la suite, no solo las pruebas nuevas.

## Instalar

Extraer el ZIP **en la raíz** `C:\AI-Factory` (sin carpeta intermedia). No sobrescribe archivos previos.

```powershell
cd C:\AI-Factory
powershell -ExecutionPolicy Bypass -File .\scripts\check-sandbox.ps1
```

**No se requiere iniciar FreeLLMAPI.** La prueba usa Git local y Python, nunca scripts producidos por IA.

## Límite de seguridad

Git Worktree **NO** aísla procesos, credenciales, rutas del host ni la red. Por eso, `ExecutionPolicy.require()` deniega toda ejecución hasta integrar un backend real (Windows Sandbox / contenedor correctamente restringido), verificar su disponibilidad y probar que no puede alcanzar el equipo anfitrión. Un booleano `sandbox_attested=True` no concede permisos. Tampoco se ejecutan pruebas de software generado por IA en el host.

No se modifican `brain/store.py`, `engine/runtime.py`, `engine/autonomous.py`, `freellmapi`, claves ni configuraciones previas.

## Próxima fase

Comprobar las capacidades reales del Windows del operador; elegir backend de aislamiento y conectar ejecutor de desarrollo **solamente** cuando la separación de procesos y archivos haya sido validada. Requiere controles específicos para red, límites de memoria/CPU/tiempo, montajes y credenciales. No suponer que Windows Sandbox está instalado.
