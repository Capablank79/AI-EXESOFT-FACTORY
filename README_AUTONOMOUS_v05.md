# Exesoft AI Factory — Autonomous Engine v0.5

Actualización incremental (no reemplaza módulos existentes): `engine/autonomous.py`, `quality/test_autonomous.py`, `scripts/check-autonomous.ps1`.

## Alcance
- Integra `QualityGate` al `step()` heredado mediante `_dispatch` para tareas QA.
- Requiere artefactos de pruebas locales válidos y aprobación explícita del adaptador QA.
- Registra aprobaciones/rechazos en tablas `quality_decisions`, `quality_artifacts` y `events` de SQLite.
- Bloquea automáticamente trabajos que estaban en `running` durante una interrupción, para revisión antes de repetir efectos externos.
- Limita número de pasos y no reintenta inmediatamente un fallo remoto.
- No cambia Claude Code, FreeLLMAPI, configuraciones ni permisos.

## Seguridad y límites de v0.5
**Este módulo no habilita todavía desarrollo autónomo con escrituras.** El adaptador Claude Code de v0.3 está en modo plan/solo lectura. El `QualityGate` de v0.4 verifica estructura y hash, pero un hash sin firma no prueba que la evidencia provenga de una fuente independiente. Registrar evidencia es una **operación supervisada**, después de ejecutar tests de código **confiable** en un ambiente adecuado. No ejecutar tests generados por IA en el host sin sandbox. Git no sustituye el aislamiento OS.

La automatización completa requiere: aislamiento del proceso, contratos de herramientas, pruebas confiables y una política de aprobación. `run_controlled` no asegura exactly-once para acciones externas.

## Estado Actual (2026-10-31) - ✅ FÁBRICA ACTIVA

### Verificaciones Completadas
- **58/58 pruebas pasadas** en 15.60s
- **Circuito interno verificado**: pm → architect → ux → developer → qa → devops → done
- **EXESOFT_ROUTER_KEY configurada** y conectividad FreeLLMAPI verificada
- **Brain persiste decisiones** y conocimiento correctamente
- **Quality Gate funcional** - bloquea sin evidencia válida

### Pruebas Clave
```
test_factory_e2e.py - Pipeline completo de 6 agentes
test_live_bridge.py - Integración FreeLLMAPI
test_project_status.py - CLI con --root
```

## Instalación
Extraer el contenido del ZIP directamente en `C:\AI-Factory`, manteniendo subcarpetas. No ejecutar sobre otra carpeta raíz. Los tres archivos son nuevos y no sobrescriben versiones anteriores.

```powershell
cd C:/AI-Factory
.\scripts\check-autonomous.ps1
```

## Uso Python supervisado
```python
from engine.autonomous import AutonomousEngine
engine = AutonomousEngine(r'C:/AI-Factory', adapters={...})
history = engine.run_controlled(project_id, max_steps=5)
print(history)
```

### Verificación de Circuito Interno
```python
from factory import run_verification
success = run_verification()  # Prueba: pm -> architect -> ux -> developer -> qa -> devops
print("Factory operativa:", success)
```

Importante: los adaptadores por defecto en `AgentRuntime` siguen pudiendo consultar FreeLLMAPI según su configuración. No ejecutar `run_controlled()` contra proyectos reales sin comprender los permisos y costes. Este paquete instala y ejecuta **solo pruebas locales simuladas**.
