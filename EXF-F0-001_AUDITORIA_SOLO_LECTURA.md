# EXF-F0-001 --- AUDITORÍA DE SOLO LECTURA

**Proyecto:** `C:\AI-Factory`\
**Estado:** AUTORIZADA\
**Dependencias:** Ninguna

## Objetivo

Producir un diagnóstico verificable del proyecto y del PostgreSQL ya
instalado para preparar F1.

## Instrucción principal

Inspecciona el proyecto y PostgreSQL en modo exclusivamente de lectura.
No modifiques archivos ni el sistema. No crees bases, roles, esquemas ni
tablas. No instales paquetes/extensiones. No ejecutes migraciones.

## A. Auditoría de EXESOFT AI FACTORY

1.  Inventariar la estructura real.
2.  Identificar entrypoints y flujos actuales (`run`, `resume`, `status`
    u otros).
3.  Localizar todas las dependencias SQLite: imports, conexiones, SQL,
    funciones específicas, tests y scripts.
4.  Mapear módulos con acceso directo a persistencia y sus dependencias.
5.  Inspeccionar Brain, ODD, Runtime, Orchestrator, agentes, Quality
    Gate, Sandy adapter, Model Gateway y configuración.
6.  Identificar pruebas existentes, cómo ejecutarlas y cuáles tienen
    efectos laterales. No ejecutar Sandy ni pruebas ACL/Registro.
7.  Detectar documentación desactualizada y contradicciones entre
    código, tests y documentación.

## B. Auditoría de PostgreSQL

Usar solo consultas: - versión cliente/servidor; - servicio Windows y
estado; - host/puerto y disponibilidad; - nombres de bases existentes,
sin inspeccionar datos sensibles; - roles existentes, sin mostrar hashes
ni secretos; - extensiones disponibles/instaladas, especialmente
`vector`; - método de autenticación y disponibilidad de acceso
administrativo.

No mostrar contraseñas, tokens ni cadenas de conexión con secretos. Si
faltan credenciales, informar el bloqueo; no intentar cambiarlas.

## C. Diseño propuesto para F1

Entregar propuesta concreta de: 1. base `exesoft_factory`; 2. roles
`exesoft_factory_migrator` y `exesoft_factory_app`, con privilegios
mínimos; 3. esquema y tablas por dominio; 4. claves, índices,
restricciones y `tenant_id`; 5. migraciones SQL numeradas y checksums;
6. uso de JSONB, FTS y pgvector, distinguiendo disponibilidad real de
recomendación; 7. orden para desacoplar SQLite sin romper contratos; 8.
estrategia de pruebas y rollback.

No inventar versiones, rutas, resultados ni credenciales. Distinguir
hallazgos confirmados, inferencias y recomendaciones.

## D. Formato obligatorio

1.  Resumen ejecutivo.
2.  Inventario de archivos/módulos.
3.  Mapa de dependencias SQLite.
4.  Estado PostgreSQL con evidencia de comandos de solo lectura.
5.  Matriz de riesgos y bloqueos.
6.  Diseño F1 recomendado.
7.  Secuencia de implementación.
8.  Comandos de solo lectura ejecutados.
9.  Confirmación explícita de que no se modificaron archivos, bases,
    roles, ACL, Registro ni configuración.

## Criterios de aceptación

-   Hallazgos importantes respaldados por rutas o salidas observadas.
-   No confundir extensión recomendada con instalada.
-   No afirmar que PostgreSQL está listo si no se verificó
    autenticación.
-   No ejecutar operaciones de escritura ni pruebas con efectos
    laterales.
-   Entregar plan F1 con dependencias, riesgos y criterios de
    aceptación.

## Límite de trabajo

Al terminar, marcar la tarea `READY_FOR_REVIEW` y DETENERSE. No
implementar F1. No crear la base. No modificar archivos. Esperar
aprobación explícita del operador.
