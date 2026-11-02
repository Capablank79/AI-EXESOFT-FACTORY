---
name: correccion-privilegios-sandbox
description: Corrección de detección de privilegios para Windows Sandbox
metadata:
  type: decision
  date: 2026-10-31
---

## Corrección: Detección de Privilegios para Windows Sandbox

### Causa del Problema
El método `_check_sandbox_available()` en `engine/sandbox_adapter.py` usaba:
1. `DISM /online /get-featureinfo` - Requiere privilegios de administrador
2. `PowerShell Get-WindowsOptionalFeature` - También requiere admin

Esto causaba que el adaptador informara que necesitaba privilegios cuando
el Sandbox ya estaba instalado y funcional.

### Solución Implementada
Cambiar la verificación a:
1. Verificar existencia de `C:\Windows\System32\WindowsSandbox.exe`
2. Fallback con comando `where WindowsSandbox.exe`

Estos métodos **no requieren privilegios de administrador** porque solo
realizan lecturas del sistema de archivos.

### Código Modificado
Archivo: `engine/sandbox_adapter.py`, líneas 51-96

```python
def _check_sandbox_available(self) -> None:
    # Verificar si el ejecutable WindowsSandbox.exe está disponible
    # Esto no requiere privilegios de administrador
    exe_path = Path("C:/Windows/System32/WindowsSandbox.exe")
    if exe_path.exists():
        return  # Sandbox executable found - feature is installed
```

### Resultados de Pruebas
- ✅ Todos los tests pasan (58/58)
- ✅ WindowsSandboxAdapter se crea sin privilegios
- ✅ `test_sandbox_not_available_without_feature` - PASSED

### Seguridad Conservada
- El aislamiento sigue siendo igual (WSB con .wsb)
- No hay cambios en la ejecución dentro del Sandbox
- Solo se cambió el método de verificación de disponibilidad

### Relacionadas con
- [[correccion-privilegios-sandbox]] - Este documento
- [[writer-adapter-creado]] - Adaptador de escritura controlada