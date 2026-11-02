# EXF-F1-001 — Resultado de creación de base y roles

**Estado:** `READY_FOR_REVIEW`  
**Fecha de registro:** 2026-11-01  
**Instancia objetivo:** PostgreSQL 18.6 local, `127.0.0.1:5432`

## Alcance

Se registra exclusivamente el cierre documental de EXF-F1-001. No se ejecutó SQL ni se modificó PostgreSQL durante la preparación de este informe. EXF-F1-001 no incluyó esquemas, tablas, índices, extensiones ni migraciones; Codex no ejecutó operaciones de ese tipo. F1-002 permanece bloqueada hasta aprobación expresa.

## Resultados confirmados por el operador

- Base `exesoft_factory` creada.
- Roles `exesoft_factory_migrator` y `exesoft_factory_app` creados.
- Contraseñas establecidas mediante entrada interactiva en `psql`; no se incluyen secretos.
- Conexión SCRAM interactiva de ambos roles exitosa.
- Verificación de privilegios y permisos correcta.
- Bases preexistentes preservadas.

## Procedencia y límites de evidencia

La evidencia de ejecución y las comprobaciones anteriores son una confirmación explícita del operador recibida el 2026-11-01. Codex no repitió conexiones ni consultas SQL para respetar la instrucción de no modificar PostgreSQL. No se proporcionó transcripción de consultas/salidas ni se registran comandos exactos ejecutados; por tanto, los resultados se atribuyen al operador y no se presentan como mediciones independientes de Codex. No se solicitaron ni almacenaron contraseñas.

En la auditoría F0 se había confirmado desde este entorno el servicio PostgreSQL 18 activo, el listener local en 5432 y SCRAM en `pg_hba.conf`; la conexión administrativa de ese entorno falló por falta de contraseña disponible en Codex. Esa limitación no invalida la confirmación interactiva posterior del operador, pero explica por qué esta validación se atribuye al operador.

## Objetos y alcance

El alcance reportado comprende únicamente la base y los dos roles definidos por EXF-F1-001. Según confirmación del operador, las bases preexistentes se preservaron. Codex no modificó configuración, `pg_hba.conf`, Firewall, ACL, Registro o servicio ni creó esquemas o tablas; estas tareas pertenecen a F1-002.

## Criterios de aceptación

| Criterio | Estado informado |
|---|---|
| Base nueva y dos roles con nombres definidos | Confirmado por el operador |
| Contraseñas por entrada interactiva | Confirmado por el operador |
| Conexión SCRAM de ambos roles | Exitosa, según operador |
| Privilegios y permisos | Correctos, según operador |
| Bases existentes sin cambios | Confirmado por el operador |
| Sin esquemas/tablas/migraciones | No ejecutados por Codex en este cierre; F1-002 no iniciada |
| Secretos ausentes del informe | Confirmado mediante revisión de este documento |

## Estado siguiente

`EXF-F1-001` queda `READY_FOR_REVIEW`; no equivale a `APPROVED`. `EXF-F1-002` continúa `BLOCKED` hasta aprobación expresa del operador. No se avanzó a esa tarea.
