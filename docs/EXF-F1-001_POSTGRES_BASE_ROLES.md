# EXF-F1-001 --- Crear base PostgreSQL y roles de Factory

**Estado:** AUTHORIZED\
**Dependencia:** EXF-F0-001 aprobada por el operador\
**Instancia objetivo:** PostgreSQL 18.6 local, `127.0.0.1:5432`\
**Base nueva:** `exesoft_factory`

## Objetivo

Crear desde cero la base exclusiva de EXESOFT AI FACTORY y dos roles
separados: uno para futuras migraciones y otro para ejecución de la
aplicación.

## Instrucción de ejecución

Trabaja exclusivamente en esta tarea. Lee `MASTER_ROADMAP.md`,
`TASK_BACKLOG.md` y este documento antes de ejecutar. La arquitectura
aprobada es inmutable salvo autorización del operador.

### 1. Preflight obligatorio

1.  Confirmar que el servicio PostgreSQL 18.6 está operativo y que la
    conexión administrativa interactiva funciona.
2.  Consultar `SHOW password_encryption`; debe ser `scram-sha-256`. Si
    no lo es, detenerse y reportar. No cambiar configuración.
3.  Consultar `pg_database` y `pg_roles` para comprobar que no existen
    `exesoft_factory`, `exesoft_factory_migrator` ni
    `exesoft_factory_app`.
4.  Si alguno de esos nombres ya existe, detenerse. No reutilizar,
    modificar ni eliminar objetos existentes.
5.  Confirmar que las cuatro bases preexistentes (`postgres`,
    `ia_autonomous_commerce`, `agente_forense_db`,
    `agente_forense_test`) permanecen fuera del alcance.

### 2. Crear roles

Crear únicamente estos roles:

-   `exesoft_factory_migrator`: LOGIN; sin SUPERUSER, CREATEDB,
    CREATEROLE, REPLICATION ni BYPASSRLS.
-   `exesoft_factory_app`: LOGIN; sin SUPERUSER, CREATEDB, CREATEROLE,
    REPLICATION ni BYPASSRLS.

Las contraseñas deben establecerse interactivamente mediante
`\password nombre_rol` en `psql`. No incluir contraseñas en SQL,
scripts, argumentos de proceso, variables de entorno, archivos, commits
ni logs. No pedir al operador que las comparta en el chat.

Si el entorno no permite introducir las contraseñas de forma interactiva
y segura, detenerse y solicitar al operador que complete ese paso en su
terminal.

### 3. Crear base y permisos

Crear `exesoft_factory` con:

-   `ENCODING 'UTF8'`
-   `TEMPLATE template0`
-   propietario administrativo `postgres`

Después: - Revocar todos los privilegios de base de datos a `PUBLIC`. -
Conceder `CONNECT` a `exesoft_factory_migrator` y
`exesoft_factory_app`. - Conceder `CREATE` sobre esta base únicamente a
`exesoft_factory_migrator`, necesario para que F1-002 cree el esquema
que administrará. - No conceder `CREATE` al rol de aplicación. - No
crear todavía esquemas, tablas, índices, extensiones ni migraciones.

### 4. Verificación obligatoria

Comprobar mediante consultas de solo lectura:

-   La base existe, usa UTF8 y pertenece a `postgres`.
-   Ambos roles existen y pueden iniciar sesión.
-   Ninguno tiene privilegios administrativos ni `BYPASSRLS`.
-   Ambos tienen `CONNECT` en `exesoft_factory`.
-   Solo el migrador tiene `CREATE` en la base.
-   `PUBLIC` no conserva privilegios en la base nueva.
-   Las cuatro bases preexistentes y sus ACL no fueron modificadas.

Probar la autenticación de cada rol con conexiones interactivas a
`exesoft_factory`. No registrar contraseñas. Si una prueba falla, no
relajar autenticación ni permisos; detenerse y reportar.

## Restricciones estrictas

-   No modificar las bases existentes, sus roles, propietarios ni ACL.
-   No cambiar `pg_hba.conf`, `postgresql.conf`, Firewall, servicio
    Windows ni Registro.
-   No instalar PostgreSQL, paquetes, extensiones ni pgvector.
-   No crear tablas ni esquemas; eso corresponde a F1-002.
-   No introducir SQLite ni importar datos históricos.
-   No guardar secretos en el repositorio o logs.
-   No ejecutar tareas de F1-002 ni posteriores.
-   No borrar automáticamente objetos creados si una verificación falla.
    Detenerse, conservar evidencia y solicitar decisión del operador.

## Entregables

1.  Base `exesoft_factory` creada y verificada.
2.  Roles `exesoft_factory_migrator` y `exesoft_factory_app` creados y
    verificados.
3.  Informe `EXF-F1-001` con comandos ejecutados, resultados, permisos
    efectivos y cualquier limitación. Nunca incluir contraseñas.
4.  Actualización del estado de esta tarea en `TASK_BACKLOG.md`. No
    alterar decisiones arquitectónicas.

## Criterios de aceptación

-   Los objetos nuevos existen con los nombres y atributos exactos.
-   Los permisos efectivos coinciden con el diseño.
-   Las conexiones interactivas de ambos roles funcionan.
-   Las bases preexistentes no fueron modificadas.
-   No se crearon esquemas, tablas ni extensiones.
-   No hay secretos en archivos ni logs.

Al completar, marcar `EXF-F1-001` como `READY_FOR_REVIEW` y DETENERSE.
No comenzar F1-002 hasta la aprobación expresa del operador.
