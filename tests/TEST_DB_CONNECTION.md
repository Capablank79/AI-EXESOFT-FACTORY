# Pruebas de Conexión PostgreSQL - F2-001

## Resumen

Capa de conexión PostgreSQL implementada con Psycopg 3 y psycopg_pool.

## Archivos Principales

| Archivo | Descripción |
|---------|-------------|
| `infrastructure/db.py` | Pool de conexiones, context managers, health check |
| `infrastructure/__init__.py` | Exports del módulo |
| `tests/test_db_connection.py` | Pruebas automatizadas (pytest) |
| `scripts/test_db_manual.py` | Script de prueba manual |

## Pruebas Implementadas

### 1. test_connection_basic
- **Objetivo:** Verificar construcción correcta de URL de conexión
- **Verifica:** 
  - URL construida con formato correcto
  - Configuración cargada desde variables de entorno

### 2. test_pool_reuse
- **Objetivo:** Verificar pool de conexiones reutilizable
- **Verifica:**
  - Pool creado correctamente
  - Singleton funciona (mismo pool en múltiples llamadas)
  - Estadísticas del pool accesibles

### 3. test_connection_close
- **Objetivo:** Verificar cierre ordenado de conexiones
- **Verifica:**
  - Pool puede cerrarse explícitamente
  - Estado correcto después del cierre

### 4. test_connection_recovery
- **Objetivo:** Verificar recuperación de errores
- **Verifica:**
  - Pool puede recrearse tras cierre
  - Manejo de excepciones

### 5. test_health_check
- **Objetivo:** Verificar health check SQL real
- **Verifica:**
  - Función callable
  - Ejecuta SELECT 1
  - Retorna booleano

### 6. test_pool_configurable_timeouts
- **Objetivo:** Verificar timeouts configurables
- **Verifica:**
  - min_size configurable
  - max_size configurable
  - timeout configurable

### 7. test_context_manager
- **Objetivo:** Verificar context managers
- **Verifica:**
  - get_connection() funciona como context manager

### 8. test_test_connection
- **Objetivo:** Verificar función de diagnóstico
- **Verifica:**
  - Return dict con success, user, database, version

## Uso

```python
from infrastructure import get_connection, health_check, test_connection

# Usar context manager
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM table")

# Health check
if health_check():
    print("Database connected")

# Diagnóstico completo
result = test_connection()
print(result)
```

## Variables de Entorno

| Variable | Descripción | Valor por defecto |
|----------|-------------|-------------------|
| POSTGRES_HOST | Host de PostgreSQL | 127.0.0.1 |
| POSTGRES_PORT | Puerto PostgreSQL | 5432 |
| POSTGRES_DB | Nombre de la base de datos | exesoft_factory |
| POSTGRES_APP_USER | Usuario de aplicación | exesoft_factory_app |
| POSTGRES_APP_PASSWORD | Password de aplicación | (requerido) |
| POSTGRES_MIGRATOR_PASSWORD | Fallback password | (requerido si APP no está) |

## Excepciones

- `DatabaseError` - Error general de base de datos
- `ConnectionTimeoutError` - Timeout al obtener conexión
- `HealthCheckError` - Error en health check

## Estado

**READY_FOR_REVIEW** - Componentes implementados y pruebas diseñadas.
