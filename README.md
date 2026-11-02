# Exesoft AI Factory — Core v0.1

Paquete inicial del **Brain + esqueleto persistente del orquestador**, preparado para incorporarse a `C:\AI-Factory` sin tocar `freellmapi/`.

## Instalación

1. Extraer este ZIP en `C:\AI-Factory` **fusionando carpetas**. Antes, verificar que archivos homónimos no contengan trabajo propio (especialmente `engine/`, `brain/`, `CLAUDE.md`).
2. Ejecutar: `powershell -ExecutionPolicy Bypass -File C:\AI-Factory\scripts\install.ps1`
3. Validar: `powershell -ExecutionPolicy Bypass -File C:\AI-Factory\scripts\check.ps1`

No requiere pip ni conexión a Internet: solo Python 3.12 y SQLite incluido en Python.

## Comandos CLI del núcleo

- `python factory.py create "Nombre" "Solicitud de proyecto"` crea el registro y workspace; **no inicia desarrollo**.
- `python factory.py plan ID` crea 5 tareas plantilla persistidas (no planificación IA).
- `python factory.py status ID` consulta estado y dependencias.
- `python factory.py remember "Título" "Contenido" --source "fuente"`
- `python factory.py recall "término"`

## Límites honestos

Todavía NO es una fábrica autónoma: no usa modelos ni Claude Code, no ejecuta QA de los proyectos, ni reanuda procesos externos. El objetivo de este paquete es construir una base comprobable sin contaminar el proyecto. Consulta `PROMPT_CLAUDE_NEXT.md` para la siguiente tarea.

**Seguridad:** no guardar API keys en Git, no publicar la base de memoria ni ejecutar clientes automáticamente.
