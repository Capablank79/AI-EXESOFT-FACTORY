"""Motor de migraciones SQL para EXESOFT AI FACTORY.

SQL versionado sin ORM. Soporta:
- Transacciones por migración
- Advisory locks para concurrencia
- Checksums SHA-256
- Verificación de dependencias
- Historial inmutable

Uso:
    python -m migrations.migration_engine up
    python -m migrations.migration_engine status
"""

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
from psycopg_pool import ConnectionPool


@dataclass
class Migration:
    """Representa una migración individual."""
    version: str  # e.g., "001__domains.sql"
    path: Path
    checksum: str
    description: str = ""
    dependencies: list[str] = None

    @property
    def number(self) -> str:
        return self.version.split("__")[0]


class MigrationEngine:
    """Motor de migraciones para PostgreSQL."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = ConnectionPool(database_url, open=False)
        self.migrations_dir = Path(__file__).parent / "domains"

    def _load_migrations(self) -> list[Migration]:
        """Carga todas las migraciones en orden."""
        migrations = []

        # Primero la tabla de schema_migrations
        schema_path = Path(__file__).parent / "000__schema_migrations.sql"
        if schema_path.exists():
            migrations.append(Migration(
                version="000__schema_migrations.sql",
                path=schema_path,
                checksum=self._compute_checksum(schema_path),
                description="Registro central de migraciones"
            ))

        # Luego las migraciones de dominios
        if self.migrations_dir.exists():
            for sql_file in sorted(self.migrations_dir.glob("*.sql")):
                migrations.append(Migration(
                    version=sql_file.name,
                    path=sql_file,
                    checksum=self._compute_checksum(sql_file),
                    dependencies=self._extract_dependencies(sql_file)
                ))

        return migrations

    def _compute_checksum(self, file_path: Path) -> str:
        """Calcula SHA-256 del archivo."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _extract_dependencies(self, file_path: Path) -> list[str]:
        """Extrae dependencias de comentarios en el archivo SQL."""
        dependencies = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("-- Dependencia:"):
                    dep = line.split(":", 1)[1].strip()
                    dependencies.append(dep)
        return dependencies

    def _get_lock_key(self, migration_version: str) -> int:
        """Genera key para advisory lock."""
        # PostgreSQL's one-argument advisory lock accepts a signed BIGINT.
        # Convert the 64-bit MD5 prefix from unsigned to two's-complement.
        key = int(hashlib.md5(f"migration_{migration_version}".encode()).hexdigest()[:16], 16)
        return key - (1 << 64) if key >= (1 << 63) else key

    @staticmethod
    def _verify_test_database_identity(conn) -> None:
        """Confirm actual DB identity before guarded integration operations."""
        if os.environ.get('EXESOFT_TEST_DATABASE_REQUIRED') != '1':
            return
        expected = os.environ.get('EXESOFT_TEST_POSTGRES_DB')
        if expected != 'exesoft_test_f7004':
            raise RuntimeError('PostgreSQL integration database is not authorized')
        with conn.cursor() as cursor:
            cursor.execute('SELECT current_database()')
            if cursor.fetchone()[0] != expected:
                raise RuntimeError('Connected PostgreSQL database identity mismatch')

    def test_connection(self) -> dict:
        """Prueba la conexión a PostgreSQL.

        Retorna:
            dict con 'success', 'user', 'database', 'error'
        """
        try:
            with psycopg.connect(self.database_url) as conn:
                self._verify_test_database_identity(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT current_user, current_database()")
                    user, db = cur.fetchone()
                    return {
                        'success': True,
                        'user': user,
                        'database': db
                    }
        except Exception as e:
            return {
                'success': False,
                'error': type(e).__name__
            }

    def up(self) -> dict:
        """Aplica migraciones pendientes.

        Retorna:
            dict con estado de cada migración aplicada
        """
        self.pool.open()
        results = {"applied": [], "skipped": [], "failed": [], "total": 0}

        try:
            with self.pool.connection() as conn:
                self._verify_test_database_identity(conn)
                with conn.cursor() as cur:
                    # Verificar que schema_migrations existe
                    cur.execute("""
                        SELECT EXISTS(
                            SELECT 1 FROM information_schema.tables
                            WHERE table_name = 'schema_migrations'
                        )
                    """)
                    if not cur.fetchone()[0]:
                        # Primera ejecución: crear tablas base
                        base_sql_path = self.migrations_dir.parent / "000__schema_migrations.sql"
                        if base_sql_path.exists():
                            with open(base_sql_path, "r", encoding="utf-8") as f:
                                cur.execute(f.read())
                            conn.commit()

                    migrations = self._load_migrations()

                    for migration in migrations:
                        results["total"] += 1

                        # Verificar si ya está aplicada
                        cur.execute(
                            "SELECT checksum FROM schema_migrations WHERE version = %s",
                            (migration.version,)
                        )
                        existing = cur.fetchone()

                        if existing:
                            # Verificar checksum
                            if existing[0] != migration.checksum:
                                results["failed"].append({
                                    "version": migration.version,
                                    "error": "Checksum alterado",
                                    "expected": migration.checksum,
                                    "actual": existing[0]
                                })
                            else:
                                results["skipped"].append(migration.version)
                            continue

                        # Verificar dependencias
                        if migration.dependencies:
                            for dep in migration.dependencies:
                                cur.execute(
                                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                                    (dep,)
                                )
                                if not cur.fetchone():
                                    results["failed"].append({
                                        "version": migration.version,
                                        "error": f"Dependencia no satisfecha: {dep}"
                                    })
                                    break
                            else:
                                # Aplicar migración con lock
                                lock_key = self._get_lock_key(migration.version)
                                cur.execute("SELECT pg_advisory_lock(%s)", (lock_key,))

                                try:
                                    with open(migration.path, "r", encoding="utf-8") as f:
                                        sql = f.read()
                                    cur.execute(sql)
                                    conn.commit()

                                    # Registrar migración
                                    cur.execute(
                                        "INSERT INTO schema_migrations(version, checksum, description, applied_at) VALUES (%s, %s, %s, NOW())",
                                        (migration.version, migration.checksum, migration.description)
                                    )
                                    conn.commit()

                                    results["applied"].append(migration.version)
                                except Exception:
                                    conn.rollback()
                                    raise
                                finally:
                                    cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
                        else:
                            # Primera migración (schema_migrations)
                            lock_key = self._get_lock_key(migration.version)
                            cur.execute("SELECT pg_advisory_lock(%s)", (lock_key,))

                            try:
                                with open(migration.path, "r", encoding="utf-8") as f:
                                    sql = f.read()
                                cur.execute(sql)
                                conn.commit()

                                cur.execute(
                                    "INSERT INTO schema_migrations(version, checksum, description, applied_at) VALUES (%s, %s, %s, NOW())",
                                    (migration.version, migration.checksum, migration.description)
                                )
                                conn.commit()

                                results["applied"].append(migration.version)
                            except Exception:
                                conn.rollback()
                                raise
                            finally:
                                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))

            return results

        finally:
            self.pool.close()

    def status(self) -> dict:
        """Muestra estado de migraciones."""
        self.pool.open()
        results = {
            "applied": [], "pending": [], "missing": [],
            "checksum_mismatches": [],
        }

        try:
            migrations = self._load_migrations()

            with self.pool.connection() as conn:
                self._verify_test_database_identity(conn)
                with conn.cursor() as cur:
                    cur.execute('SELECT version, checksum, applied_at FROM schema_migrations')
                    recorded = {row[0]: row for row in cur.fetchall()}
                    for migration in migrations:
                        existing = recorded.pop(migration.version, None)
                        if existing:
                            results["applied"].append({
                                "version": migration.version,
                                "applied_at": existing[2].isoformat()
                            })
                            if existing[1] != migration.checksum:
                                results["checksum_mismatches"].append({
                                    "version": migration.version,
                                    "expected": migration.checksum,
                                    "actual": existing[1],
                                })
                        else:
                            results["pending"].append({
                                "version": migration.version,
                                "checksum": migration.checksum
                            })
                    results['missing'] = sorted(recorded)
        finally:
            self.pool.close()

        return results


def get_db_url() -> str:
    """Build the migration URL from process environment or the project .env.

    Credentials and database identity have no built-in defaults. Missing values
    fail closed rather than silently targeting the production database.
    """
    env_path = Path(__file__).parent.parent / ".env"
    file_config = {}
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    file_config[key.strip()] = value.strip().strip("\"'")
        except OSError as exc:
            raise RuntimeError("Could not read PostgreSQL migration configuration") from exc

    config = {
        'user': os.environ.get('POSTGRES_MIGRATOR_USER') or file_config.get('POSTGRES_MIGRATOR_USER'),
        'password': os.environ.get('POSTGRES_MIGRATOR_PASSWORD') or file_config.get('POSTGRES_MIGRATOR_PASSWORD'),
        'host': os.environ.get('POSTGRES_HOST') or file_config.get('POSTGRES_HOST') or '127.0.0.1',
        'port': os.environ.get('POSTGRES_PORT') or file_config.get('POSTGRES_PORT') or '5432',
        # Require an explicit process target so a .env production default can
        # never turn a bare migration command into production DDL.
        'db': os.environ.get('POSTGRES_DB'),
    }
    missing = [key for key in ('user', 'password', 'db') if not config[key]]
    if missing:
        raise RuntimeError(
            'Missing required PostgreSQL migration configuration: '
            + ', '.join(missing)
        )
    if os.environ.get('EXESOFT_TEST_DATABASE_REQUIRED') == '1':
        expected = os.environ.get('EXESOFT_TEST_POSTGRES_DB')
        if (expected != 'exesoft_test_f7004' or config['db'] != expected
                or os.environ.get('POSTGRES_DB') != expected):
            raise RuntimeError('Migration test connection must target exesoft_test_f7004')
    return psycopg.conninfo.make_conninfo(
        host=config['host'], port=config['port'], dbname=config['db'],
        user=config['user'], password=config['password'],
    )

def main():
    """CLI simple para el motor de migraciones."""
    if len(sys.argv) < 2:
        print("Uso: python -m migrations.migration_engine [up|status|test]")
        sys.exit(1)

    command = sys.argv[1]

    db_url = get_db_url()

    # No imprimir la URL completa (contiene password)
    print("Conectando a PostgreSQL...")

    engine = MigrationEngine(db_url)

    if command == "test":
        result = engine.test_connection()
        if result['success']:
            print("Conexion OK - Usuario:", result['user'], ", BD:", result['database'])
        else:
            error_msg = result['error'].replace('�', '?')
            print("Conexion FALLIDA - Error:", error_msg)
            sys.exit(1)
    elif command == "up":
        results = engine.up()
        print(f"Migraciones aplicadas: {len(results['applied'])}")
        print(f"Migraciones omitidas: {len(results['skipped'])}")
        if results['failed']:
            print(f"Migraciones fallidas: {results['failed']}")
            sys.exit(1)
    elif command == "status":
        status = engine.status()
        print("Migraciones aplicadas:")
        for m in status['applied']:
            print(f"  OK {m['version']} ({m['applied_at']})")
        print("Migraciones pendientes:")
        for m in status['pending']:
            print(f"  PENDIENTE {m['version']}")
        if status['checksum_mismatches']:
            print("Migraciones con checksum inconsistente:")
            for item in status['checksum_mismatches']:
                print(f"  ERROR {item['version']}")
        if status['missing']:
            print("Versiones aplicadas sin archivo local:")
            for version in status['missing']:
                print(f"  ERROR {version}")
        if status['checksum_mismatches'] or status['missing']:
            sys.exit(1)
    else:
        print(f"Comando desconocido: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
