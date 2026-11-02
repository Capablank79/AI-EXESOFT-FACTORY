# EXESOFT AI FACTORY — Instrucciones para Claude Code

Trabajar únicamente en la fábrica (no crear aplicaciones de ejemplo). No modificar `freellmapi/`, dependencias, ni credenciales sin autorización explícita.

Objetivo: fábrica autónoma con Brain persistente, orquestador de estados, ODD, agentes especializados, validación ejecutable y entrega controlada.

Antes de implementar: inspeccionar `docs/ARCHITECTURE.md`, `agents/CONTRACT.md`, `methodology/ODD.md` y código existente. Aplicar ODD proporcional; no regenerar todo para una corrección localizada.

Reglas:
- Sin invenciones sobre tests: ejecutar y registrar evidencias.
- Nunca publicar, gastar dinero, eliminar archivos fuera del workspace o exponer secretos sin autorización.
- Aislar implementaciones en ramas Git y no alterar `freellmapi/`.
- Evitar prompts enormes; consultar Brain y adjuntar solo contexto necesario.
- Si falta un detalle secundario, decidir técnicamente y registrar la decisión; escalar solo temas de alto impacto.
