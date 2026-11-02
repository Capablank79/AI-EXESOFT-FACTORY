# Próxima instrucción para Claude Code

Actúa como ingeniero principal de Exesoft AI Factory. Trabaja exclusivamente en `C:\AI-Factory` y nunca modifiques `freellmapi/`. Lee `CLAUDE.md`, `docs/ARCHITECTURE.md`, `agents/CONTRACT.md` y `methodology/ODD.md`.

Tarea: extender el núcleo existente para implementar el **runtime persistente de ejecución de tareas**, en un cambio acotado: estados robustos, recuperación de tareas interrumpidas, límites de reintentos, eventos y evidencia. No conectes todavía Claude Code ni invoques modelos; no desarrolles apps de ejemplo. Produce tests automatizados reproducibles que fallen antes y pasen después. Documenta decisiones y limitaciones. Pide autorización antes de tocar dependencias, credenciales o ejecutar operaciones destructivas.
