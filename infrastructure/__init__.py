"""Infrastructure module for EXESOFT AI FACTORY.

Capa de infraestructura proporcionando:
- Conexión PostgreSQL con pool de conexiones
- Health check y diagnóstico
- Gestión de configuración
"""

from .db import (
    get_connection,
    get_db_url,
    get_config,
    get_pool,
    close_pool,
    health_check,
    test_connection,
    configure,
    reset_pool,
    get_pool_stats,
    DBConfig,
    PoolConfig,
    DatabaseError,
    ConnectionTimeoutError,
)

__all__ = [
    "get_connection",
    "get_db_url",
    "get_config",
    "get_pool",
    "close_pool",
    "health_check",
    "test_connection",
    "configure",
    "reset_pool",
    "get_pool_stats",
    "DBConfig",
    "PoolConfig",
    "DatabaseError",
    "ConnectionTimeoutError",
]