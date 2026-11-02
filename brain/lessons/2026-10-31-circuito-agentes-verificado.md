---
name: circuito-agentes-verificado
description: Lección sobre verificación exitosa del circuito de agentes
metadata:
  type: lesson
  date: 2026-10-31
---

## Lección: Verificación del Circuito de Agentes

### Qué aprendimos

1. **El pipeline de 6 agentes funciona correctamente**
   - Dependencias se resuelven correctamente (pm → architect → ux → developer → qa → devops)
   - El Quality Gate bloquea sin evidencia válida
   - Los estados se persisten correctamente en SQLite

2. **ManualAdapter es útil para pruebas**
   - Permite verificar el flujo sin necesidad de conectividad LLM
   - Ideal para verificación inicial de circuitos

3. **La estructura de dependencias ODD es correcta**
   - Cada tarea tiene una dependencia explícita
   - No hay tareas listas hasta que la anterior complete

### Qué funcionó

- `AutonomousEngine.run_controlled()` ejecuta el pipeline completo
- `verify_test_evidence()` valida correctamente la evidencia
- `register_test_evidence()` persiste la evidencia en Brain

### Qué falló

- Windows Sandbox requiere privilegios de administrador
- FreeLLMAPI necesita clave API (ya resuelto)

### Recomendación para el futuro

Para verificar un nuevo circuito:
1. Crear adaptadores Mock con Result(True, ...)
2. Registrar evidencia de pruebas pasadas
3. Ejecutar `run_controlled()` y verificar estado final 'done'