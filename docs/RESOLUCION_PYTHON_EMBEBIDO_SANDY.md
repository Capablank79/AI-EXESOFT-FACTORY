# Resolución de Python embebido en Sandy

## Problema

La validación inicial del paquete rechazaba `python312.zip` con el mensaje
«no contiene una biblioteca estándar aislada». El CPython embebible oficial
3.12.10 x64 contiene la biblioteca estándar como `.pyc` dentro del ZIP, no como
`.py`, y añade `site-packages/README.txt` como marcador vacío. El validador
trataba ese marcador como si fueran paquetes instalados.

Después de corregir esa validación, Sandy podía iniciar el proceso, pero Python
terminaba con `0xC0000135` dentro del AppContainer. El mismo ejecutable dio
código 0 fuera de Sandy bajo JLLV. La diferencia estaba en el acceso efectivo
del sandbox al árbol aislado de DLL: el TOML concedía acceso a la raíz del
workspace, pero no declaraba explícitamente el directorio del runtime.

## Solución aplicada

- `engine/sandy_adapter.py` acepta dentro de `python312.zip` solo
  `encodings/__init__.pyc` como requisito de biblioteca estándar y, si aparece
  `site-packages`, solo acepta el marcador oficial exacto
  `site-packages/README.txt` con su contenido esperado. Cualquier módulo o ruta
  adicional sigue fallando cerrado.
- Se conservan la comprobación del checksum publicado, la arquitectura PE x64,
  `python312._pth` (`python312.zip` y `.`), la ausencia de paquetes instalados y
  la extracción del runtime dentro del workspace temporal.
- Para ejecuciones de Python, Sandy recibe en `allow.deep` tanto la raíz del
  workspace como el directorio del runtime embebido. El adaptador resuelve la
  ruta y exige que sea un directorio propio dentro del workspace; no concede
  permisos al Python global, al perfil ni al proyecto fuente.
- El preflight mantiene el orden `cmd.exe` → Python. La recuperación y limpieza
  de Sandy permanecen bajo el procedimiento controlado existente.

## Validación

Pruebas locales sin Sandy ni cambios ACL: **19/19 aprobadas**. Incluyeron la
estructura del ZIP oficial, el preparador en un directorio temporal, rechazo de
paquetes en `site-packages`, rutas de búsqueda y arquitectura PE.

Validación real ejecutada por el operador bajo JLLV, el 1 de noviembre de 2026:

```text
Python/ejecución y escritura en workspace: PASS
Denegación de red: PASS
Timeout y SHA-256: PASS
Denegación de escritura fuera del workspace: PASS
Resultado: 4/4; exit 0
Sandy --status: no active instances or recovery state
```

El ZIP original no se modificó. La prueba real valida el circuito completo; el
log de Sandy no identifica por nombre una DLL individual, así que no se atribuye
el código NTSTATUS a una DLL concreta.

## Repetir la validación

Desde PowerShell normal bajo JLLV, en `C:\AI-Factory`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate-sandy.ps1
& "$env:USERPROFILE\Sandy-Evaluation\sandy.exe" --status
```

Las pruebas crean recursos AppContainer y ACL temporales propios. El script
comprueba antes que Sandy esté limpio y omite la limpieza global cuando no puede
probar que los recursos residuales pertenecen al workspace de validación.
