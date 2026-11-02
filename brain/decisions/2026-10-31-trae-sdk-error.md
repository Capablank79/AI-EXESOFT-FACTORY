---
name: trae-sdk-error
description: Error de inicialización del SDK TRAE Sandbox
metadata:
  type: decision
  date: 2026-10-31
---

## Error de Inicialización del SDK TRAE Sandbox

### Error Exacto

```
TRAE Sandbox Error: init failed
System { message: "init: SBoxSDK_Init failed: SDK initialization failed" }
```

### Causa Identificada

1. **El ejecutable está instalado**: `C:\Users\...\trae-sandbox.exe` existe
2. **El CLI funciona**: Comandos `help`, `environment-check` responden
3. **El SDK NO funciona**: `SBoxSDK_Init` falla con `sdk_load_failed`

### Diagnóstico del Environment-Check

```
EnvironmentCheckResult {
    supported: false,
    kernel_ready: false,
    dependencies_installed: false,
    sdk_load_failed,
    configured: false
}
```

### Backend FFI

El backend `ffi` documentado en los docs de TRAE no funciona porque:
- Requiere dependencias del SDK que no están presentes
- Necesita WSL2 o Docker Desktop con backend WSL2
- Falta el kernel de sandbox

### Decisión

**TRAE Sandbox NO se puede integrar en este entorno.**

### Motivos

1. SDK initialization fallida - dependencias faltantes
2. No hay hypervisor disponible (WSL2/Docker)
3. Windows Sandbox es la única alternativa viable actualmente

### Acción Tomada

1. `TraeSandboxAdapter._verify_installation()` actualizado para detectar `supported: false`
2. El adaptador reporta `available: False` correctamente
3. `WindowsSandboxAdapter` sigue como única opción viable
4. `ManualAdapter` como fallback para pruebas sin aislamiento

### Evidencia

- Ejecutable: ✅ Presente
- CLI: ✅ Funciona
- SDK: ❌ `sdk_load_failed`
- Sandbox: ❌ No disponible (sin admin)

### Relacionadas con
- [[trae-adapter-creado]] - Adaptador implementado
- [[trae-sdk-error]] - Este documento