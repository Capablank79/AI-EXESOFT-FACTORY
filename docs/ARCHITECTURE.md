# Exesoft AI Factory — arquitectura acordada

- **Meta:** una solicitud de producto inicia planificación, desarrollo, verificación, correcciones y entrega con mínima intervención del operador.
- **Brain:** SQLite transaccional + Markdown versionable; FTS5 para recuperar conocimiento. La búsqueda semántica y Engram son ampliaciones posteriores.
- **Orquestador:** Python 3.12; máquina de estados, dependencias, eventos y reanudación.
- **Metodología:** ODD proporcional al impacto, no repetir fases indiscriminadamente.
- **Executor:** Claude Code supervisado; conexión FreeLLMAPI ya validada previamente por el operador en otro equipo, pendiente de prueba final en esta máquina.
- **QA:** pruebas reales y evidencias ligadas al commit; un LLM no puede declararse aprobado a sí mismo.
- **Seguridad:** aislamiento por workspace, Git, autorización para cambios destructivos y despliegues.
- **Costo:** respetar cuotas de proveedores y limitar trabajos/reintentos.

## Estado de implementación

Disponible en el paquete: almacenamiento SQLite, FTS5, eventos, creación de proyectos, tareas persistidas, dependencias, cálculo de tareas disponibles, CLI, pruebas de núcleo. **No disponibles aún:** integración LLM/Claude, ejecución autónoma de tareas, UI, QA ejecutable sobre repositorios, recuperación de procesos en curso, permisos.
