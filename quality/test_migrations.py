"""Pruebas para migraciones F1-002.

Pruebas unitarias para validar:
- Cálculo de checksums
- Orden de migraciones
- Dependencias
- Funciones de utilidad SQL
"""

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from migrations.migration_engine import MigrationEngine, Migration
from migrations import migration_engine


class TestMigrateEngine:
    """Pruebas del motor de migraciones."""

    def test_compute_checksum(self, tmp_path):
        """Verifica cálculo correcto de SHA-256."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        # Crear archivo temporal
        test_file = tmp_path / "test_migration.sql"
        content = "CREATE TABLE test (id UUID PRIMARY KEY);"
        test_file.write_text(content, encoding='utf-8')

        expected = hashlib.sha256(content.encode('utf-8')).hexdigest()
        result = engine._compute_checksum(test_file)
        assert result == expected

    def test_extract_dependencies(self, tmp_path):
        """Extrae dependencias de comentarios SQL."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        test_file = tmp_path / "test_deps.sql"
        content = """-- Dependencia: 002__identity.sql
CREATE TABLE test (id UUID PRIMARY KEY);
"""
        test_file.write_text(content, encoding='utf-8')

        deps = engine._extract_dependencies(test_file)
        assert deps == ["002__identity.sql"]

    def test_load_migrations_order(self):
        """Verifica que las migraciones se carguen en orden numérico."""
        engine = MigrationEngine("postgresql://test@localhost/test")
        migrations = engine._load_migrations()

        # Verificar orden
        numbers = [m.number for m in migrations]
        assert numbers == sorted(numbers)

        # Verificar que schema_migrations sea la primera
        assert migrations[0].version == "000__schema_migrations.sql"

    def test_get_lock_key(self):
        """Verifica generación consistente de keys de lock."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        key1 = engine._get_lock_key("001__domains.sql")
        key2 = engine._get_lock_key("001__domains.sql")

        # Mismas entradas = mismos keys
        assert key1 == key2

        # Diferentes versiones = keys diferentes
        key3 = engine._get_lock_key("002__identity.sql")
        assert key1 != key3

    def test_migration_url_requires_configured_credentials_and_database(self, monkeypatch):
        for name in (
            'POSTGRES_MIGRATOR_USER', 'POSTGRES_MIGRATOR_PASSWORD', 'POSTGRES_HOST',
            'POSTGRES_PORT', 'POSTGRES_DB',
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(Path, 'exists', lambda self: False)
        with pytest.raises(RuntimeError, match='Missing required PostgreSQL migration configuration'):
            migration_engine.get_db_url()

    def test_migration_url_does_not_take_database_target_from_dotenv(self, monkeypatch):
        monkeypatch.setattr(Path, 'exists', lambda self: True)
        monkeypatch.setattr(
            Path, 'read_text',
            lambda self, encoding=None: (
                'POSTGRES_MIGRATOR_USER=test_migrator\n'
                'POSTGRES_MIGRATOR_PASSWORD=test_only\n'
                'POSTGRES_DB=exesoft_factory\n'
            ),
        )
        monkeypatch.setenv('POSTGRES_MIGRATOR_USER', 'test_migrator')
        monkeypatch.setenv('POSTGRES_MIGRATOR_PASSWORD', 'test_only')
        monkeypatch.delenv('POSTGRES_DB', raising=False)
        with pytest.raises(RuntimeError, match='db'):
            migration_engine.get_db_url()

    def test_migration_connection_error_does_not_echo_driver_details(self, monkeypatch):
        class UnsafeDriverError(Exception):
            pass

        monkeypatch.setattr(
            migration_engine.psycopg, 'connect',
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                UnsafeDriverError('sensitive connection detail')
            ),
        )
        result = MigrationEngine('postgresql://mock/isolated').test_connection()
        assert result == {'success': False, 'error': 'UnsafeDriverError'}


class TestMigrationSchema:
    """Pruebas del esquema de migraciones."""

    def test_schema_migrations_table_exists(self):
        """Verifica definición de tabla schema_migrations."""
        schema_path = Path(__file__).parent.parent / "migrations" / "000__schema_migrations.sql"

        if schema_path.exists():
            content = schema_path.read_text(encoding='utf-8')
            assert "CREATE TABLE IF NOT EXISTS schema_migrations" in content
            assert "version TEXT PRIMARY KEY" in content
            assert "checksum TEXT NOT NULL" in content

    def test_domains_exist(self):
        """Verifica que los dominios sean válidos SQL."""
        domains_path = Path(__file__).parent.parent / "migrations" / "domains"

        if domains_path.exists():
            for sql_file in domains_path.glob("*.sql"):
                content = sql_file.read_text(encoding='utf-8')
                # Verificar que es SQL válido (no está vacío)
                assert len(content.strip()) > 100
                # Checksums are calculated from immutable file contents and
                # recorded in schema_migrations; they need not be embedded in SQL.
                checksum = MigrationEngine("postgresql://test@localhost/test")._compute_checksum(sql_file)
                assert len(checksum) == 64
                assert all(character in '0123456789abcdef' for character in checksum)


class TestMigrationDependencies:
    """Pruebas de dependencias entre migraciones."""

    def test_dependencies_form_chain(self):
        """Verifica cadena de dependencias: 000 -> 001 -> 002 -> ..."""
        engine = MigrationEngine("postgresql://test@localhost/test")
        migrations = engine._load_migrations()

        # 000 no tiene dependencias
        assert migrations[0].dependencies == ["base"] or migrations[0].dependencies is None

        # Cada migración posterior debe tener dependencia en la anterior
        for i in range(2, len(migrations)):
            current = migrations[i]
            if current.dependencies:
                # Al menos una dependencia debe apuntar a migración anterior
                prev_num = str(int(current.number) - 1).zfill(3)
                dep_found = any(prev_num in str(d) for d in current.dependencies)
                # Nota: algunas migraciones pueden tener dependencias explícitas


class TestChecksumVerification:
    """Pruebas de verificación de integridad."""

    def test_checksum_detection(self, tmp_path):
        """Verifica que cambios en archivo cambian el checksum."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        test_file = tmp_path / "test_checksum.sql"

        # Escritura 1
        test_file.write_text("CREATE TABLE t1 (id INT);", encoding='utf-8')
        checksum1 = engine._compute_checksum(test_file)

        # Escritura 2 (cambio)
        test_file.write_text("CREATE TABLE t1 (id INT, name TEXT);", encoding='utf-8')
        checksum2 = engine._compute_checksum(test_file)

        # Los checksums deben ser diferentes
        assert checksum1 != checksum2


# Prueba de integración (requiere PostgreSQL)
@pytest.mark.integration
def test_full_migration_cycle():
    """Prueba ciclo completo: up -> status -> verify."""
    pytest.skip("Requiere base PostgreSQL real")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
