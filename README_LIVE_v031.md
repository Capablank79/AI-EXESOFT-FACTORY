# Exesoft AI Factory — Integración real supervisada v0.3.1

Actualización **aditiva** del Core v0.1.1 + Runtime v0.2 + Agent Runtime v0.3.

## Instalación en Windows
Extraer **el contenido del ZIP** directamente en `C:\AI-Factory`, de forma que exista `C:\AI-Factory\scripts\verify-live.py`. El ZIP no incluye carpeta contenedora ni sustituye archivos previos.

```powershell
cd C:\AI-Factory
powershell -ExecutionPolicy Bypass -File .\scripts\check-live.ps1
python .\scripts\verify-live.py
```

El último comando pide la clave unificada en formato oculto; no la imprime ni la guarda. También acepta `EXESOFT_ROUTER_KEY` como variable de entorno temporal. No compartir credenciales.

## Alcance exacto
- Envía **una** solicitud textual al router local, modelo `gpt-oss-120b`.
- Crea un proyecto de **verificación de infraestructura**, no una aplicación de ejemplo.
- El Agent Runtime recupera el contexto pertinente mediante el Brain FTS5.
- El Runtime persiste el `task_run` y, si tuvo éxito, escribe la respuesta en `knowledge`.
- Verifica la persistencia de los dos registros con consultas SQLite.
- Falla con estado distinto de cero si no hubo inferencia satisfactoria.
- No toca FreeLLMAPI, no cambia credenciales, no usa Claude Code ni ejecuta comandos del modelo.

**Importante:** cada invocación genera un proyecto de validación y consume una llamada al proveedor. No ejecutar en bucle. La consulta puede tener costo/cuota según el proveedor que seleccione el router.

## Límites pendientes
Este paso no demuestra desarrollo autónomo, calidad semántica de la respuesta, ni atribución del proveedor: el `model` de la respuesta API debe contrastarse con el historial del router. La ejecución global de un proyecto de software y QA real pertenecen a fases posteriores.
