"""Capa de conexión PostgreSQL para EXESOFT AI FACTORY.

Implementa:
- Pool de conexiones reutilizable con psycopg_pool
- Configuración desde .env sin secretos en código
- Conexiones con exesoft_factory_app y privilegios mínimos
- Gestión de apertura, cierre y recuperación de conexiones
- Timeouts configurables
- Manejo de errores
- Context managers para adquirir y liberar conexiones
- Health check SQL real

Autor: Claude Code - EXESOFT AI FACTORY
"""

import os
import logging
from contextlib import contextmanager
from typing import Optional, Generator
from dataclasses import dataclass
from psycopg_pool import ConnectionPool, PoolTimeout
import psycopg
from psycopg import Error, OperationalError

logger = logging.getLogger(__name__)
from infrastructure.observability import log_event


@dataclass
class DBConfig:
    """Configuración de base de datos desde entorno."""
    user: str
    password: str
    host: str
    port: int
    db: str

    @classmethod
    def from_env(cls) -> 'DBConfig':
        """Construye configuración desde variables de entorno."""
        user = os.environ.get('POSTGRES_APP_USER', 'exesoft_factory_app')
        password = os.environ.get('POSTGRES_APP_PASSWORD') or os.environ.get('POSTGRES_MIGRATOR_PASSWORD')
        host = os.environ.get('POSTGRES_HOST', '127.0.0.1')
        port = int(os.environ.get('POSTGRES_PORT', '5432'))
        db = os.environ.get('POSTGRES_DB', 'exesoft_factory')

        if os.environ.get('EXESOFT_TEST_DATABASE_REQUIRED') == '1':
            expected = os.environ.get('EXESOFT_TEST_POSTGRES_DB', '')
            if (not expected or db != expected or not db.startswith('exesoft_test_')
                    or db in {'exesoft_factory', 'exesoft_backup_fixture_f7003_20261105',
                              'exesoft_restore_f7003_20261105'}):
                raise ValueError('PostgreSQL test guard requires an isolated exesoft_test_* database')

        if not password:
            raise ValueError(
                "POSTGRES_APP_PASSWORD no configurado en entorno. "
                "Set POSTGRES_APP_PASSWORD o POSTGRES_MIGRATOR_PASSWORD."
            )

        return cls(user=user, password=password, host=host, port=port, db=db)

    @property
    def conninfo(self) -> str:
        """URL de conexión (sin exponer password en logs)."""
        return f"postgresql://{self.user}:***@{self.host}:{self.port}/{self.db}"

    def to_url(self) -> str:
        """URL completa de conexión."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


@dataclass
class PoolConfig:
    """Configuración del pool de conexiones."""
    min_size: int = 2
    max_size: int = 10
    timeout: float = 30.0  # Timeout para obtener conexión del pool
    session_init: Optional[str] = None  # SQL para inicializar sesión


class DatabaseError(Exception):
    """Excepción personalizada para errores de base de datos."""
    pass


class ConnectionTimeoutError(DatabaseError):
    """Error de timeout al obtener conexión."""
    pass


class HealthCheckError(DatabaseError):
    """Error en health check."""
    pass


# Singleton del pool
_pool: Optional[ConnectionPool] = None
_config: Optional[DBConfig] = None
_pool_config: PoolConfig = PoolConfig()


def configure(pool_config: Optional[PoolConfig] = None) -> None:
    """Configurar pool de conexiones.

    Args:
        pool_config: Configuración opcional del pool
    """
    global _pool_config
    if pool_config:
        _pool_config = pool_config


def get_config() -> DBConfig:
    """Obtener configuración (singleton)."""
    global _config
    if _config is None:
        _config = DBConfig.from_env()
    return _config


def get_pool() -> ConnectionPool:
    """Obtener pool de conexiones (singleton).

    Inicializa el pool si no existe.

    Returns:
        ConnectionPool instance
    """
    global _pool
    if _pool is None:
        config = get_config()
        conninfo = config.to_url()

        # Crear pool con configuración
        _pool = ConnectionPool(
            conninfo=conninfo,
            min_size=_pool_config.min_size,
            max_size=_pool_config.max_size,
            timeout=_pool_config.timeout,
            open=False  # Se abre explícitamente
        )
        _pool.open()
        log_event(logger, logging.INFO, 'postgres.pool.opened', component='postgres',
                  status='opened')

    return _pool


def reset_pool() -> None:
    """Resetear el pool (para testing)."""
    global _pool, _config
    if _pool is not None:
        try:
            _pool.close()
        except Exception as e:
            log_event(logger, logging.WARNING, 'postgres.pool.close_failed', component='postgres',
                      error_type=type(e).__name__)
        _pool = None
    _config = None
    _config = None


@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    """Context manager para adquirir y liberar conexiones.

    Uso:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM table")

    Yields:
        Conexión psycopg.Connection

    Raises:
        ConnectionTimeoutError: Si no se puede obtener conexión en timeout
        DatabaseError: Si hay error de conexión
    """
    pool = get_pool()

    try:
        with pool.connection() as conn:
            yield conn
    except PoolTimeout as e:
        log_event(logger, logging.ERROR, 'postgres.pool.timeout', component='postgres',
                  error_type=type(e).__name__)
        raise ConnectionTimeoutError(f"Timeout obteniendo conexión: {e}") from e
    except OperationalError as e:
        log_event(logger, logging.ERROR, 'postgres.connection.failed', component='postgres',
                  error_type=type(e).__name__)
        raise DatabaseError(f"Error de conexión: {e}") from e
    except Exception as e:
        log_event(logger, logging.ERROR, 'postgres.connection.failed', component='postgres',
                  error_type=type(e).__name__)
        raise


def close_pool() -> None:
    """Cerrar ordenadamente el pool de conexiones."""
    global _pool, _config
    if _pool is not None:
        try:
            _pool.close()
            log_event(logger, logging.INFO, 'postgres.pool.closed', component='postgres')
        except Exception as e:
            log_event(logger, logging.ERROR, 'postgres.pool.close_failed', component='postgres',
                      error_type=type(e).__name__)
        finally:
            _pool = None
            _config = None


def health_check() -> bool:
    """Health check SQL real.

    Ejecuta SELECT 1 para verificar conectividad.

    Returns:
        True si el health check pasa, False en caso contrario
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 as health_check")
                result = cur.fetchone()
                return result is not None and result[0] == 1
    except Exception as e:
        log_event(logger, logging.ERROR, 'postgres.health.failed', component='postgres',
                  check='connectivity', error_type=type(e).__name__, outcome='unavailable')
        return False


def test_connection() -> dict:
    """Probar conexión y devolver información.

    Returns:
        dict con 'success', 'user', 'database', 'error'
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Verificar usuario y base actuales
                cur.execute("SELECT current_user, current_database()")
                user, db = cur.fetchone()

                # Verificar version
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]

                return {
                    'success': True,
                    'user': user,
                    'database': db,
                    'version': version
                }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'user': None,
            'database': None,
            'version': None
        }


def get_pool_stats() -> dict:
    """Obtener estadísticas del pool.

    Returns:
        dict con información del pool
    """
    pool = _pool
    if pool is None:
        return {'status': 'not_opened'}

    return {
        'status': 'opened',
        'min_size': _pool_config.min_size,
        'max_size': _pool_config.max_size,
        'timeout': _pool_config.timeout,
        'closed': pool.closed
    }


# Función de conveniencia para uso rápido
def get_db_url() -> str:
    """Construir URL de base de datos desde .env o parámetros.

    Lee .env desde el directorio raiz del proyecto.
    Prioridad: POSTGRES_APP_PASSWORD > POSTGRES_MIGRATOR_PASSWORD.
    """
    return get_config().to_url()


# Exportar para uso como módulo
__all__ = [
    'DBConfig',
    'PoolConfig',
    'DatabaseError',
    'ConnectionTimeoutError',
    'HealthCheckError',
    'get_db_url',
    'get_config',
    'get_pool',
    'get_connection',
    'close_pool',
    'health_check',
    'test_connection',
    'configure',
    'reset_pool',
    'get_pool_stats',
]
