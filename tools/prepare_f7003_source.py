"""Safely prepare the pre-provisioned F7-003 source fixture with migrations.

Run only after an authorized administrator has created the dedicated database
and assigned it to the existing configured migration account. The script will
not connect to or run migrations on the production database.
"""
from __future__ import annotations

import sys
import os

import psycopg
from psycopg.conninfo import make_conninfo

from migrations.migration_engine import MigrationEngine
from tools.postgres_backup import BackupError, connection_settings


SOURCE = "exesoft_backup_fixture_f7003_20261105"
PRODUCTION = "exesoft_factory"


def main() -> int:
    try:
        settings = connection_settings()
        if settings["database"] != SOURCE or settings["database"] == PRODUCTION:
            raise BackupError("POSTGRES_DB must identify the exact isolated F7-003 source fixture")
        if os.environ.get("EXESOFT_BACKUP_TEST_SOURCE_CONFIRMED") != SOURCE:
            raise BackupError("Exact source fixture provenance confirmation is required")
        dsn = make_conninfo(
            host=settings["host"], port=settings["port"], dbname=settings["database"],
            user=settings["user"], password=settings["password"], connect_timeout=10,
        )
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT current_database(), current_user, r.rolsuper,
                           pg_get_userbyid(d.datdba)
                    FROM pg_catalog.pg_roles r
                    JOIN pg_catalog.pg_database d ON d.datname = current_database()
                    WHERE r.rolname = current_user
                """)
                identity = cur.fetchone()
        if not identity or identity[0] != SOURCE or identity[0] == PRODUCTION:
            raise BackupError("Connected database identity did not match the isolated fixture")
        if identity[2]:
            raise BackupError("Fixture migration must use the configured non-superuser account")
        if identity[1] != settings["user"] or identity[3] != settings["user"]:
            raise BackupError("Configured migration account must own the isolated source database")

        engine = MigrationEngine(dsn)
        expected = {migration.version for migration in engine._load_migrations()}
        approved = {"000__schema_migrations.sql"} | {
            f"{version:03d}__{name}.sql" for version, name in [
                (1, "domains"), (2, "identity"), (3, "projects"), (4, "planning"),
                (5, "execution"), (6, "memory"), (7, "events"), (8, "quality"),
                (9, "providers"), (10, "artifacts"), (11, "indexes"),
                (12, "integrity_security"), (13, "security_defaults_indexes"),
                (14, "tenant_edge_guards"), (15, "fts_odd_documents"),
                (16, "f3004_idempotency_constraints"), (17, "f3004_task_version"),
            ]
        }
        if expected != approved:
            raise BackupError("Migration set differs from the approved 000–017 fixture set")
        result = engine.up()
        if result["failed"]:
            raise BackupError("One or more fixture migrations failed")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database()")
                if cur.fetchone()[0] != SOURCE:
                    raise BackupError("Post-migration connection did not remain on the fixture")
                cur.execute("SELECT version FROM public.schema_migrations")
                applied = {row[0] for row in cur.fetchall()}
        if applied != approved:
            raise BackupError("Fixture migration history is incomplete or contains unexpected versions")
        print("F7-003 source fixture migrations 000–017 verified.")
        return 0
    except (BackupError, psycopg.Error) as exc:
        print(f"F7-003 fixture preparation stopped: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
