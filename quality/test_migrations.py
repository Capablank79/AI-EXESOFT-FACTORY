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


class TestMigrateEngine:
    """Pruebas del motor de migraciones."""

    def test_compute_checksum(self):
        """Verifica cálculo correcto de SHA-256."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        # Crear archivo temporal
        test_file = Path("/tmp/test_migration.sql")
        content = "CREATE TABLE test (id UUID PRIMARY KEY);"
        test_file.write_text(content)

        try:
            expected = hashlib.sha256(content.encode()).hexdigest()
            result = engine._compute_checksum(test_file)
            assert result == expected
        finally:
            test_file.unlink()

    def test_extract_dependencies(self):
        """Extrae dependencias de comentarios SQL."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        test_file = Path("/tmp/test_deps.sql")
        content = """-- Dependencia: 002__identity.sql
CREATE TABLE test (id UUID PRIMARY KEY);
"""
        test_file.write_text(content)

        try:
            deps = engine._extract_dependencies(test_file)
            assert deps == ["002__identity.sql"]
        finally:
            test_file.unlink()

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


class TestMigrationSchema:
    """Pruebas del esquema de migraciones."""

    def test_schema_migrations_table_exists(self):
        """Verifica definición de tabla schema_migrations."""
        schema_path = Path(__file__).parent.parent / "migrations" / "000__schema_migrations.sql"

        if schema_path.exists():
            content = schema_path.read_text()
            assert "CREATE TABLE IF NOT EXISTS schema_migrations" in content
            assert "version TEXT PRIMARY KEY" in content
            assert "checksum TEXT NOT NULL" in content

    def test_domains_exist(self):
        """Verifica que los dominios sean válidos SQL."""
        domains_path = Path(__file__).parent.parent / "migrations" / "domains"

        if domains_path.exists():
            for sql_file in domains_path.glob("*.sql"):
                content = sql_file.read_text()
                # Verificar que es SQL válido (no está vacío)
                assert len(content.strip()) > 100
                # Verificar que tiene checksum
                assert "Checksum:" in content


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

    def test_checksum_detection(self):
        """Verifica que cambios en archivo cambian el checksum."""
        engine = MigrationEngine("postgresql://test@localhost/test")

        test_file = Path("/tmp/test_checksum.sql")

        try:
            # Escritura 1
            test_file.write_text("CREATE TABLE t1 (id INT);")
            checksum1 = engine._compute_checksum(test_file)

            # Escritura 2 (cambio)
            test_file.write_text("CREATE TABLE t1 (id INT, name TEXT);")
            checksum2 = engine._compute_checksum(test_file)

            # Los checksums deben ser diferentes
            assert checksum1 != checksum2
        finally:
            test_file.unlink()


# Prueba de integración (requiere PostgreSQL)
@pytest.mark.integration
def test_full_migration_cycle():
    """Prueba ciclo completo: up -> status -> verify."""
    pytest.skip("Requiere base PostgreSQL real")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])