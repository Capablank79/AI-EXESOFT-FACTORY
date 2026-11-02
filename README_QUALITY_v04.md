# EXESOFT AI FACTORY — Quality Engine v0.4

## Alcance real

Parche incremental. No reemplaza `brain/store.py`, `engine/runtime.py`, `engine/agents.py` ni modifica `freellmapi`. Agrega módulos independientes:

- `quality/evidence.py`: huellas SHA-256 de archivos y ejecución local real de tests Python vía `unittest` (directorio `tests/` de cada proyecto).
- `engine/quality_gate.py`: decide si un resultado tiene artefactos de pruebas ejecutadas correctamente.
- `quality/git_isolation.py`: inicializa Git **solo en un workspace sin repositorio previo** y permite consultar cambios.
- `quality/test_quality.py`: pruebas de éxito, fallo, manipulación de evidencia, traversal y Git.

**No se han integrado estos módulos al flujo automático de `Runtime.step()` todavía.** El QA v0.2 previo acepta referencias declaradas; por tanto, no se debe decir que la fábrica completa queda protegida hasta integrar el gate y probarlo end-to-end. El siguiente paso es integrar el motor bajo ejecución autorizada e instrumentar auditoría SQLite.

## Seguridad y limitaciones

1. Solo ejecutar `run_tests()` para código de proyecto previamente aprobado: los tests ejecutan Python arbitrario y aún **no** existe sandbox de SO (contenedor/VM). `subprocess` limita duración, no permisos ni red.
2. Los archivos de evidencia se guardan en el propio workspace, no son inmutables ni firmados. El hash del log detecta cambios accidentales, **no protege contra un atacante que edite simultáneamente el log y el hash**. No constituye certificación antifraude.
3. No se habilita ejecución automática de código generado por IA.
4. Git init no crea commits ni branches ni toca repositorios existentes. Es una base de aislamiento lógico, **no un sandbox de seguridad**.
5. No se ejecuta ningún comando propuesto por el modelo; el único comando de test está predefinido.

## Instalación

Extraer el contenido de este ZIP directamente en `C:\AI-Factory` preservando rutas. Este paquete no lleva carpeta contenedora.

```powershell
cd C:\AI-Factory
powershell -ExecutionPolicy Bypass -File .\scripts\check-quality.ps1
```

**Resultado esperado:** pruebas existentes + 8 pruebas nuevas. No asumir un número fijo de pruebas si se añadieron otras en el proyecto.
