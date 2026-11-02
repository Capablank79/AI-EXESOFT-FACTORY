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

    def test_connection(self) -> dict:
        """Prueba la conexión a PostgreSQL.

        Retorna:
            dict con 'success', 'user', 'database', 'error'
        """
        try:
            with psycopg.connect(self.database_url) as conn:
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
                'error': str(e)
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
        results = {"applied": [], "pending": [], "missing": []}

        try:
            migrations = self._load_migrations()

            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    for migration in migrations:
                        cur.execute(
                            "SELECT version, applied_at FROM schema_migrations WHERE version = %s",
                            (migration.version,)
                        )
                        existing = cur.fetchone()

                        if existing:
                            results["applied"].append({
                                "version": migration.version,
                                "applied_at": existing[1].isoformat()
                            })
                        else:
                            results["pending"].append({
                                "version": migration.version,
                                "checksum": migration.checksum
                            })
        finally:
            self.pool.close()

        return results


def get_db_url() -> str:
    """Construye URL de base de datos desde .env o parámetros.

    Lee .env desde el directorio raiz del proyecto.
    Prioridad: parámetro de configuracion > .env > valores por defecto.
    """
    # Ruta al archivo .env
    env_path = Path(__file__).parent.parent / ".env"

    # Variables con valores por defecto (usar amor2030 como especificado)
    config = {
        'user': 'exesoft_factory_migrator',
        'password': 'amor2030',  # Usar amor2030 como especificado
        'host': '127.0.0.1',
        'port': '5432',
        'db': 'exesoft_factory'
    }

    # Leer .env si existe
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()

                        if key == 'POSTGRES_MIGRATOR_USER':
                            config['user'] = value
                        elif key == 'POSTGRES_MIGRATOR_PASSWORD':
                            config['password'] = value
                        elif key == 'POSTGRES_HOST':
                            config['host'] = value
                        elif key == 'POSTGRES_PORT':
                            config['port'] = value
                        elif key == 'POSTGRES_DB':
                            config['db'] = value
        except Exception:
            pass

    # Sobrescribir con variables de entorno si están definidas
    config['password'] = os.environ.get('POSTGRES_MIGRATOR_PASSWORD', config['password'])
    config['user'] = os.environ.get('POSTGRES_MIGRATOR_USER', config['user'])
    config['host'] = os.environ.get('POSTGRES_HOST', config['host'])
    config['port'] = os.environ.get('POSTGRES_PORT', config['port'])
    config['db'] = os.environ.get('POSTGRES_DB', config['db'])

    return f"postgresql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['db']}"


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
    else:
        print(f"Comando desconocido: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
