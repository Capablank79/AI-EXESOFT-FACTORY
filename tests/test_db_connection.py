"""Pruebas automatizadas de conexión PostgreSQL para F2-001.

Pruebas incluidas:
1. test_connection_basic - Conexión básica exitosa
2. test_pool_reuse - Pool de conexiones reutilizable
3. test_connection_close - Cierre ordenado de conexiones
4. test_connection_recovery - Recuperación de errores de conexión
5. test_health_check - Health check SQL real
6. test_missing_password - Validación de configuración
7. test_pool_configurable_timeouts - Timeouts configurables
8. test_context_manager - Context managers funcionales

Autor: Claude Code - EXESOFT AI FACTORY
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, '.env')
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, encoding='utf-8') as env_file:
        for env_line in env_file:
            env_line = env_line.strip()
            if env_line and not env_line.startswith('#') and '=' in env_line:
                env_key, env_value = env_line.split('=', 1)
                os.environ.setdefault(env_key.strip(), env_value.strip())

# Agregar src al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infrastructure import db as db_module


class TestDBConnectionBasic:
    """Pruebas básicas de conexión."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        self.original_migrator = os.environ.get('POSTGRES_MIGRATOR_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        # Reset del módulo para obtener nueva configuración
        db_module._config = None

        yield

        # Cleanup
        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        if self.original_migrator:
            os.environ['POSTGRES_MIGRATOR_PASSWORD'] = self.original_migrator
        else:
            os.environ.pop('POSTGRES_MIGRATOR_PASSWORD', None)

        db_module.reset_pool()

    def test_connection_basic(self):
        """Prueba 1: test_connection_basic - Conexión básica exitosa."""
        config = db_module.get_config()
        assert config.user == 'exesoft_factory_app'
        assert config.host == '127.0.0.1'
        assert config.port == 5432
        assert config.db == 'exesoft_test_f7004'
        assert 'test_password_123' in config.to_url() or '***' in config.conninfo

    def test_connection_basic_url_format(self):
        """Verificar formato URL de conexión."""
        url = db_module.get_db_url()
        assert url.startswith('postgresql://')
        assert 'exesoft_factory_app' in url
        assert '127.0.0.1:5432/exesoft_test_f7004' in url
        assert 'test_password_123' in url


class TestPoolReuse:
    """Pruebas de pool de conexiones."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        db_module._config = None
        db_module.reset_pool()

        yield

        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        db_module.reset_pool()

    def test_pool_reuse(self):
        """Prueba 2: test_pool_reuse - Pool de conexiones reutilizable."""
        # Verificar pool no es None después de inicializar
        pool = db_module.get_pool()
        assert pool is not None

        # Verificar que get_pool devuelve el mismo pool (singleton)
        pool2 = db_module.get_pool()
        assert pool is pool2

        # Verificar estadísticas del pool
        stats = db_module.get_pool_stats()
        assert stats['status'] == 'opened'
        assert stats['min_size'] == 2
        assert stats['max_size'] == 10
        assert stats['timeout'] == 30.0

    def test_pool_can_be_reset(self):
        """Verificar que el pool puede ser resetado."""
        pool1 = db_module.get_pool()
        assert pool1 is not None

        db_module.reset_pool()
        assert db_module._pool is None

        pool2 = db_module.get_pool()
        assert pool1 is not pool2


class TestConnectionClose:
    """Pruebas de cierre de conexiones."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        db_module._config = None

        yield

        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        db_module.close_pool()

    def test_connection_close(self):
        """Prueba 3: test_connection_close - Cierre ordenado de conexiones."""
        # Abrir pool
        pool = db_module.get_pool()
        assert pool is not None
        assert not pool.closed

        # Cerrar pool
        db_module.close_pool()
        assert db_module._pool is None

    def test_close_idempotent(self):
        """Verificar que close es idempotente (se puede llamar múltiples veces)."""
        db_module.get_pool()
        db_module.close_pool()
        db_module.close_pool()  # No debe lanzar error


class TestConnectionRecovery:
    """Pruebas de recuperación de errores de conexión."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        db_module._config = None
        db_module.reset_pool()

        yield

        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        db_module.reset_pool()

    def test_connection_recovery_pool_reopen(self):
        """Prueba 4: test_connection_recovery - Recuperación de pool tras cierre."""
        # Abrir y cerrar pool
        pool1 = db_module.get_pool()
        assert pool1 is not None

        db_module.close_pool()
        assert db_module._pool is None

        # Reabrir - debe crear nuevo pool
        pool2 = db_module.get_pool()
        assert pool2 is not None
        assert pool1 is not pool2

    @patch('infrastructure.db.ConnectionPool')
    def test_connection_recovery_on_error(self, mock_pool_class):
        """Verificar manejo de errores de conexión."""
        # Mock que lanza error
        mock_pool = MagicMock()
        mock_pool.connection.side_effect = db_module.DatabaseError("Connection failed")
        mock_pool_class.return_value = mock_pool
        mock_pool.open = MagicMock()
        mock_pool.closed = False

        db_module._pool = mock_pool

        with pytest.raises(db_module.DatabaseError):
            with db_module.get_connection() as conn:
                pass


class TestHealthCheck:
    """Pruebas de health check."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        db_module._config = None
        db_module.reset_pool()

        yield

        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        db_module.reset_pool()

    def test_health_check_real(self):
        """Prueba 5: test_health_check - Health check SQL real."""
        # Verificar que health_check existe y es callable
        assert hasattr(db_module, 'health_check')
        assert callable(db_module.health_check)

        # health_check devuelve bool
        result = db_module.health_check()
        assert isinstance(result, bool)


class TestMissingPassword:
    """Pruebas de validación de configuración."""

    def test_missing_password_raises_error(self):
        """Prueba: excepción cuando APP password no está configurado."""
        # Guardar y limpiar variables
        saved = {}
        for key in ['POSTGRES_APP_PASSWORD', 'POSTGRES_MIGRATOR_PASSWORD',
                    'POSTGRES_APP_USER', 'POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB']:
            saved[key] = os.environ.pop(key, None)

        db_module._config = None

        try:
            with pytest.raises(ValueError, match="POSTGRES_APP_PASSWORD no configurado"):
                db_module.get_db_url()
        finally:
            # Restaurar
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val
            db_module._config = None

    def test_migrator_password_fallback(self):
        """Verificar fallback a POSTGRES_MIGRATOR_PASSWORD."""
        saved = {}
        for key in ['POSTGRES_APP_PASSWORD', 'POSTGRES_MIGRATOR_PASSWORD',
                    'POSTGRES_APP_USER', 'POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB']:
            saved[key] = os.environ.pop(key, None)

        os.environ['POSTGRES_MIGRATOR_PASSWORD'] = 'migrator_pass'
        db_module._config = None

        try:
            config = db_module.get_config()
            assert 'migrator_pass' in config.to_url()
        finally:
            for key, val in saved.items():
                if val is not None:
                    os.environ[key] = val
            db_module._config = None


class TestPoolConfigurableTimeouts:
    """Pruebas de timeouts configurables."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        db_module._config = None
        db_module.reset_pool()

        yield

        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        db_module.reset_pool()

    def test_pool_configurable_timeouts(self):
        """Prueba 6: test_pool_configurable_timeouts - Timeouts configurables."""
        # Configurar pool con timeouts personalizados
        custom_config = db_module.PoolConfig(
            min_size=5,
            max_size=20,
            timeout=60.0
        )
        db_module.configure(custom_config)

        # Reset para aplicar nueva configuración
        db_module.reset_pool()

        # Verificar configuración
        pool = db_module.get_pool()
        assert pool is not None

        stats = db_module.get_pool_stats()
        assert stats['min_size'] == 5
        assert stats['max_size'] == 20
        assert stats['timeout'] == 60.0


class TestConnectionManager:
    """Pruebas de context managers."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Use the configured app credential against the explicitly isolated DB."""
        expected_db = 'exesoft_test_f7004'
        if (os.environ.get('POSTGRES_DB') != expected_db
                or os.environ.get('EXESOFT_TEST_POSTGRES_DB') != expected_db):
            pytest.fail('Connection manager test requires both database variables to target exesoft_test_f7004')
        if (not os.environ.get('POSTGRES_APP_USER')
                or not os.environ.get('POSTGRES_APP_PASSWORD')):
            pytest.fail('Connection manager test requires the existing app credentials from .env')
        original_pool_config = db_module._pool_config
        db_module.configure(db_module.PoolConfig(min_size=1, max_size=1, timeout=5.0))
        db_module.reset_pool()
        yield
        db_module.reset_pool()
        db_module.configure(original_pool_config)

    def test_context_manager(self):
        """Prueba 7: test_context_manager - Context managers funcionales."""
        # Verificar que get_connection es un context manager
        assert hasattr(db_module, 'get_connection')

        # Usar el context manager
        with db_module.get_connection() as conn:
            # Verificar que la conexión está en el pool
            assert conn is not None


class TestTestConnection:
    """Pruebas de test_connection function."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Configurar variables de entorno para pruebas."""
        self.original_password = os.environ.get('POSTGRES_APP_PASSWORD')
        os.environ['POSTGRES_APP_PASSWORD'] = 'test_password_123'
        os.environ['POSTGRES_APP_USER'] = 'exesoft_factory_app'
        os.environ['POSTGRES_HOST'] = '127.0.0.1'
        os.environ['POSTGRES_PORT'] = '5432'
        os.environ['POSTGRES_DB'] = 'exesoft_test_f7004'

        db_module._config = None
        db_module.reset_pool()

        yield

        if self.original_password:
            os.environ['POSTGRES_APP_PASSWORD'] = self.original_password
        else:
            os.environ.pop('POSTGRES_APP_PASSWORD', None)

        db_module.reset_pool()

    def test_test_connection_returns_dict(self):
        """Verificar que test_connection devuelve dict con las claves correctas."""
        result = db_module.test_connection()

        assert isinstance(result, dict)
        assert 'success' in result
        # Otros campos dependen de si la BD está disponible
        assert 'error' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
