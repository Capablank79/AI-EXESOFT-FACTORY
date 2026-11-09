"""Safe unit and opt-in isolated PostgreSQL validation for F7-003."""
import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from tools import postgres_backup as backup


def make_archive(directory: Path, name: str, contents: bytes = b"archive") -> Path:
    path = directory / name
    path.write_bytes(contents)
    manifest = path.with_name(path.name.removesuffix(".dump") + ".manifest.json")
    manifest.write_text(json.dumps({
        "format": "postgresql-custom", "database": "exesoft_factory",
        "archive": path.name, "size_bytes": len(contents), "sha256": backup._digest(path),
    }), encoding="utf-8")
    return path


def user_table_counts(database: str, settings: dict) -> dict[tuple[str, str], int]:
    counts = {}
    with psycopg.connect(
        host=settings["host"], port=settings["port"], dbname=database,
        user=settings["user"], password=settings["password"], connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT schemaname, tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY schemaname, tablename
            """)
            tables = cur.fetchall()
            for schema, table in tables:
                cur.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                ))
                counts[(schema, table)] = cur.fetchone()[0]
    return counts


def schema_signature(database: str, settings: dict) -> list[tuple]:
    with psycopg.connect(
        host=settings["host"], port=settings["port"], dbname=database,
        user=settings["user"], password=settings["password"], connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_schema, table_name, column_name, data_type,
                       udt_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name, ordinal_position
            """)
            columns = cur.fetchall()
            cur.execute("""
                SELECT schemaname, tablename, indexname, indexdef
                FROM pg_catalog.pg_indexes
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY schemaname, tablename, indexname
            """)
            indexes = cur.fetchall()
            cur.execute("""
                SELECT n.nspname, c.relname, con.conname, con.contype,
                       pg_catalog.pg_get_constraintdef(con.oid, true)
                FROM pg_catalog.pg_constraint con
                JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY n.nspname, c.relname, con.conname
            """)
            constraints = cur.fetchall()
            return columns, indexes, constraints


def synthetic_project(database: str, name: str, settings: dict):
    with psycopg.connect(
        host=settings["host"], port=settings["port"], dbname=database,
        user=settings["user"], password=settings["password"], connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, request, tenant_id::text FROM public.projects WHERE name = %s",
                (name,),
            )
            return cur.fetchone()


def database_identity(database: str, settings: dict) -> tuple:
    with psycopg.connect(
        host=settings["host"], port=settings["port"], dbname=database,
        user=settings["user"], password=settings["password"], connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_database(), current_user, r.rolsuper,
                       pg_get_userbyid(d.datdba)
                FROM pg_catalog.pg_roles r
                JOIN pg_catalog.pg_database d ON d.datname = current_database()
                WHERE r.rolname = current_user
            """)
            return cur.fetchone()


def applied_migration_versions(database: str, settings: dict) -> set[str]:
    with psycopg.connect(
        host=settings["host"], port=settings["port"], dbname=database,
        user=settings["user"], password=settings["password"], connect_timeout=10,
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM public.schema_migrations")
            return {row[0] for row in cur.fetchall()}


SYNTHETIC_REQUEST = "Synthetic project created only to validate PostgreSQL backup and restore."


def validate_synthetic_fixture(snapshot: dict) -> None:
    counts = snapshot["counts"]
    nonzero = {
        (schema, table): count for (schema, table), count in counts.items()
        if table != "schema_migrations" and count
    }
    assert nonzero == {
        ("public", "tenants"): 1, ("public", "projects"): 1,
        ("public", "knowledge"): 2, ("public", "events"): 1,
        ("public", "odd_documents"): 1,
    }
    assert counts.get(("public", "tasks")) == 0
    assert snapshot["nondefault_schemas"] == []

    tenant_rows = snapshot["tenants"]
    project_rows = snapshot["projects"]
    knowledge_rows = snapshot["knowledge"]
    event_rows = snapshot["events"]
    odd_rows = snapshot["odd_documents"]
    assert len(tenant_rows) == len(project_rows) == len(event_rows) == len(odd_rows) == 1
    assert len(knowledge_rows) == 2

    tenant = tenant_rows[0]
    project = project_rows[0]
    for value in (tenant["id"], project["id"], project["tenant_id"]):
        uuid.UUID(str(value))
    assert tenant["name"].startswith("F7-003 synthetic ")
    assert tenant["slug"].startswith("f7003-synthetic-")
    assert project["tenant_id"] == tenant["id"]
    assert project["name"].startswith("F7-003 backup fixture ")
    assert project["request"] == {"description": SYNTHETIC_REQUEST}

    by_source = {row["source"]: row for row in knowledge_rows}
    assert set(by_source) == {"ODDEngine", "PostgresBrain"}
    assert all(row["tenant_id"] == tenant["id"] and row["project_id"] == project["id"]
               for row in knowledge_rows)
    assert by_source["PostgresBrain"]["title"] == f"Feature: {project['name']}"
    assert by_source["PostgresBrain"]["body"] == SYNTHETIC_REQUEST
    for row in knowledge_rows:
        uuid.UUID(str(row["id"]))
    odd_knowledge = by_source["ODDEngine"]
    odd_payload = json.loads(odd_knowledge["body"])
    assert odd_knowledge["title"] == f"Feature: {odd_payload['name']}"
    assert odd_payload["name"].startswith("F7-003 backup fixture ")
    assert odd_payload["project_id"] == str(project["id"])
    assert odd_payload["request"] == SYNTHETIC_REQUEST

    event = event_rows[0]
    uuid.UUID(str(event["id"]))
    assert event["tenant_id"] == tenant["id"] and event["project_id"] == project["id"]
    assert event["type"] == "project.created"
    assert event["detail"] == {"name": project["name"]}
    odd_document = odd_rows[0]
    odd_document_payload = json.loads(odd_document["body"])
    assert odd_document["project_id"] == project["id"]
    assert odd_document_payload["project_id"] == str(project["id"])
    assert odd_document_payload["request"] == SYNTHETIC_REQUEST


def synthetic_fixture_snapshot(database: str, settings: dict) -> dict:
    snapshot = {"counts": user_table_counts(database, settings)}
    queries = {
        "tenants": "SELECT * FROM public.tenants ORDER BY id",
        "projects": "SELECT * FROM public.projects ORDER BY id",
        "knowledge": "SELECT * FROM public.knowledge ORDER BY id",
        "events": "SELECT * FROM public.events ORDER BY id",
        "odd_documents": "SELECT * FROM public.odd_documents ORDER BY project_id",
        "schema_migrations": "SELECT * FROM public.schema_migrations ORDER BY version",
    }
    with psycopg.connect(
        host=settings["host"], port=settings["port"], dbname=database,
        user=settings["user"], password=settings["password"], connect_timeout=10,
        options="-c default_transaction_read_only=on", row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()["current_database"] == database
            cur.execute("""
                SELECT nspname FROM pg_catalog.pg_namespace
                WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast')
                ORDER BY nspname
            """)
            snapshot["nondefault_schemas"] = [row["nspname"] for row in cur.fetchall()]
            for name, query in queries.items():
                cur.execute(query)
                snapshot[name] = cur.fetchall()
    return snapshot


def test_verify_rejects_modified_archive(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, "exesoft_exesoft_factory_20261105T120000Z_a1b2c3d4.dump")
    monkeypatch.setattr(backup, "_tool", lambda _name: "pg_restore")
    monkeypatch.setattr(backup, "_run", lambda *_args: subprocess.CompletedProcess([], 0, "TOC entry", ""))
    assert backup.verify(archive)["archive"] == archive.name
    archive.write_bytes(b"tampered")
    with pytest.raises(backup.BackupError, match="integrity"):
        backup.verify(archive)


def test_prune_removes_only_expired_well_formed_backup_pairs(tmp_path):
    stale = make_archive(tmp_path, "exesoft_exesoft_factory_20260101T120000Z_a1b2c3d4.dump")
    fresh = make_archive(tmp_path, "exesoft_exesoft_factory_20261105T120000Z_b1b2c3d4.dump")
    unrelated = tmp_path / "keep.dump"
    unrelated.write_bytes(b"keep")
    old = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    os.utime(stale, (old, old))
    os.utime(stale.with_name(stale.name.removesuffix(".dump") + ".manifest.json"), (old, old))
    assert backup.prune(tmp_path, 30) == [stale.name]
    assert not stale.exists()
    assert fresh.exists() and unrelated.exists()


def test_restore_refuses_unconfirmed_or_production_target(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, "exesoft_exesoft_factory_20261105T120000Z_a1b2c3d4.dump")
    monkeypatch.setattr(backup, "_tool", lambda _name: "pg_restore")
    monkeypatch.setattr(backup, "_run", lambda *_args: subprocess.CompletedProcess([], 0, "TOC entry", ""))
    settings = {
        "host": "localhost", "port": "5432", "database": "exesoft_factory",
        "user": "tester", "password": "hidden",
    }
    monkeypatch.setattr(backup, "connection_settings", lambda: settings)
    with pytest.raises(backup.BackupError, match="prefix"):
        backup.restore_to_existing_database(archive, "exesoft_factory", confirmed_target="exesoft_factory")
    settings["database"] = "exesoft_restore_production"
    with pytest.raises(backup.BackupError, match="operator provenance"):
        backup.restore_to_existing_database(archive, "exesoft_restore_production")
    with pytest.raises(backup.BackupError, match="configured production"):
        backup.restore_to_existing_database(
            archive, "exesoft_restore_production", confirmed_target="exesoft_restore_production"
        )


def test_restore_existing_target_never_creates_or_drops_databases(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, "exesoft_exesoft_factory_20261105T120000Z_c1d2e3f4.dump")
    monkeypatch.setattr(backup, "_tool", lambda name: name)
    settings = {
        "host": "localhost", "port": "5432", "database": "exesoft_factory",
        "user": "backup_operator", "password": "secret-value-must-not-be-an-argument",
    }
    monkeypatch.setattr(backup, "connection_settings", lambda: settings)
    monkeypatch.setattr(backup, "_assert_existing_restore_target", lambda *_args: None)
    calls = []

    def fake_run(args, env):
        passfile = env.get("PGPASSFILE")
        calls.append((args, env, bool(passfile and Path(passfile).exists())))
        output = "TOC entry" if args[0] == "pg_restore" and "--list" in args else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(backup, "_run", fake_run)
    target = f"exesoft_restore_{uuid.uuid4().hex}"
    backup.restore_to_existing_database(archive, target, confirmed_target=target)
    assert [call[0][0] for call in calls] == ["pg_restore", "pg_restore"]
    assert target in calls[1][0]
    assert all(settings["password"] not in " ".join(args) for args, _env, _exists in calls)
    assert calls[1][2]
    assert not Path(calls[1][1]["PGPASSFILE"]).exists()


@pytest.mark.parametrize("has_user_objects", [False, True])
def test_restore_target_emptiness_allows_empty_and_rejects_user_objects(
    monkeypatch, has_user_objects
):
    target = "exesoft_restore_f7003_20261105"
    executed = []

    class Cursor:
        def execute(self, query):
            executed.append(query)

        def fetchone(self):
            return (target, "fixture_owner", "fixture_owner", True, has_user_objects)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    connect_args = []

    def fake_connect(**kwargs):
        connect_args.append(kwargs)
        return Connection()

    monkeypatch.setattr(backup.psycopg, "connect", fake_connect)
    settings = {
        "host": "localhost", "port": "5432", "database": "exesoft_backup_fixture_f7003_20261105",
        "user": "fixture_owner", "password": "not-logged",
    }
    if has_user_objects:
        with pytest.raises(backup.BackupError, match="not empty"):
            backup._assert_existing_restore_target(settings, target)
    else:
        backup._assert_existing_restore_target(settings, target)

    assert connect_args[0]["dbname"] == target
    query = executed[0]
    assert "current_database()" in query
    assert "pg_toast" in query
    assert "pg_class" in query and "pg_proc" in query and "pg_type" in query
    assert "pg_extension" in query


def valid_synthetic_snapshot():
    tenant_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    project_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    project_name = "F7-003 backup fixture synthetic_22222222"
    odd_name = "F7-003 backup fixture synthetic"
    odd_payload = {"project_id": str(project_id), "name": odd_name, "request": SYNTHETIC_REQUEST}
    return {
        "counts": {
            ("public", "schema_migrations"): 18, ("public", "tenants"): 1,
            ("public", "projects"): 1, ("public", "knowledge"): 2,
            ("public", "events"): 1, ("public", "odd_documents"): 1,
            ("public", "tasks"): 0,
        },
        "nondefault_schemas": [],
        "tenants": [{"id": tenant_id, "name": "F7-003 synthetic fixture", "slug": "f7003-synthetic-fixture"}],
        "projects": [{"id": project_id, "tenant_id": tenant_id, "name": project_name,
                      "request": {"description": SYNTHETIC_REQUEST}}],
        "knowledge": [
            {"id": uuid.UUID("33333333-3333-4333-8333-333333333333"),
             "tenant_id": tenant_id, "project_id": project_id, "source": "ODDEngine",
             "title": f"Feature: {odd_name}", "body": json.dumps(odd_payload)},
            {"id": uuid.UUID("44444444-4444-4444-8444-444444444444"),
             "tenant_id": tenant_id, "project_id": project_id, "source": "PostgresBrain",
             "title": f"Feature: {project_name}", "body": SYNTHETIC_REQUEST},
        ],
        "events": [{"id": uuid.UUID("55555555-5555-4555-8555-555555555555"),
                    "tenant_id": tenant_id, "project_id": project_id,
                    "type": "project.created", "detail": {"name": project_name}}],
        "odd_documents": [{"project_id": project_id, "body": json.dumps(odd_payload)}],
    }


def test_synthetic_fixture_validator_accepts_only_correlated_fixture_records():
    validate_synthetic_fixture(valid_synthetic_snapshot())


def test_synthetic_fixture_validator_rejects_additional_or_mismatched_records():
    snapshot = valid_synthetic_snapshot()
    snapshot["counts"][("public", "artifacts")] = 1
    with pytest.raises(AssertionError):
        validate_synthetic_fixture(snapshot)
    snapshot = valid_synthetic_snapshot()
    snapshot["knowledge"][0]["tenant_id"] = uuid.uuid4()
    with pytest.raises(AssertionError):
        validate_synthetic_fixture(snapshot)


@pytest.mark.skipif(os.environ.get("EXESOFT_BACKUP_TEST_ENABLED") != "1",
                    reason="Requires explicit opt-in for pre-provisioned isolated PostgreSQL databases")
def test_native_backup_restore_roundtrip_in_isolated_database(tmp_path, monkeypatch):
    """Uses pre-provisioned fixture databases and preserves both for operator review."""
    source = os.environ.get("EXESOFT_BACKUP_TEST_SOURCE_DB")
    if source and source.casefold() == "exesoft_factory":
        pytest.fail("The production database is forbidden as the test source")
    if not source or not source.startswith("exesoft_backup_fixture_"):
        pytest.skip("EXESOFT_BACKUP_TEST_SOURCE_DB must identify a disposable fixture database")
    if source != "exesoft_backup_fixture_f7003_20261105":
        pytest.fail("Source database name does not match the authorized fixture")
    if os.environ.get("EXESOFT_BACKUP_TEST_SOURCE_CONFIRMED") != source:
        pytest.fail("Set the exact source database name as operator provenance confirmation")
    target = os.environ.get("EXESOFT_BACKUP_TEST_RESTORE_DB")
    if target != "exesoft_restore_f7003_20261105" or target == source:
        pytest.fail("Restore target must be a separate exesoft_restore_* database")
    if os.environ.get("EXESOFT_BACKUP_TEST_RESTORE_CONFIRMED") != target:
        pytest.fail("Set the exact restore database name as operator provenance confirmation")
    monkeypatch.setenv("POSTGRES_DB", source)
    settings = backup.connection_settings()
    source_identity = database_identity(source, settings)
    assert source_identity[0] == source and source_identity[1] == settings["user"]
    assert source_identity[2] is False and source_identity[3] == settings["user"]
    expected_migrations = {"000__schema_migrations.sql"} | {
        f"{number:03d}__{name}.sql" for number, name in [
            (1, "domains"), (2, "identity"), (3, "projects"), (4, "planning"),
            (5, "execution"), (6, "memory"), (7, "events"), (8, "quality"),
            (9, "providers"), (10, "artifacts"), (11, "indexes"),
            (12, "integrity_security"), (13, "security_defaults_indexes"),
            (14, "tenant_edge_guards"), (15, "fts_odd_documents"),
            (16, "f3004_idempotency_constraints"), (17, "f3004_task_version"),
        ]
    }
    assert expected_migrations == applied_migration_versions(source, settings)

    source_snapshot = synthetic_fixture_snapshot(source, settings)
    validate_synthetic_fixture(source_snapshot)
    archive = backup.backup(tmp_path, retention_days=365)
    metadata = backup.verify(archive)
    assert metadata["database"] == source
    backup.restore_to_existing_database(archive, target, confirmed_target=target)

    target_identity = database_identity(target, settings)
    assert target_identity[0] == target and target_identity[1] == settings["user"]
    assert target_identity[2] is False and target_identity[3] == settings["user"]
    target_snapshot = synthetic_fixture_snapshot(target, settings)
    validate_synthetic_fixture(target_snapshot)
    assert schema_signature(source, settings) == schema_signature(target, settings)
    assert source_snapshot == target_snapshot
