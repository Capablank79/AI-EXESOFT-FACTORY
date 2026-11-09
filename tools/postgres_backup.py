"""Safe PostgreSQL logical backup, verification, and new-database restore tools.

Uses PostgreSQL's native pg_dump/pg_restore utilities. Passwords are supplied
to libpq through a short-lived PGPASSFILE and are never written to arguments,
backup metadata, or application logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_DIR = ROOT / "backups" / "postgres"
BACKUP_RE = re.compile(r"^exesoft_[a-zA-Z0-9_-]+_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}\.dump$")
RESTORE_TARGET_RE = re.compile(r"^exesoft_restore_[a-zA-Z0-9_]+$")


class BackupError(RuntimeError):
    pass


def _dotenv_values(path: Path = ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def connection_settings() -> dict[str, str]:
    file_values = _dotenv_values()
    settings = {
        "host": os.environ.get("POSTGRES_HOST", file_values.get("POSTGRES_HOST", "127.0.0.1")),
        "port": os.environ.get("POSTGRES_PORT", file_values.get("POSTGRES_PORT", "5432")),
        "database": os.environ.get("POSTGRES_DB", file_values.get("POSTGRES_DB", "exesoft_factory")),
        "user": os.environ.get("POSTGRES_MIGRATOR_USER", file_values.get("POSTGRES_MIGRATOR_USER", "exesoft_factory_migrator")),
        "password": os.environ.get("POSTGRES_MIGRATOR_PASSWORD", file_values.get("POSTGRES_MIGRATOR_PASSWORD", "")),
    }
    if not settings["password"]:
        raise BackupError("PostgreSQL password is not configured")
    if not re.fullmatch(r"[A-Za-z0-9_]+", settings["database"]):
        raise BackupError("Database name contains unsupported characters")
    return settings


def _tool(name: str) -> str:
    override = os.environ.get(name.upper().replace("-", "_"))
    if override:
        path = Path(override)
        if not path.is_file():
            raise BackupError(f"Configured {name} executable does not exist")
        return str(path)
    found = shutil.which(name)
    if found:
        return found
    # PostgreSQL Windows installers do not always add client utilities to PATH.
    if os.name == "nt":
        roots = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"]
        candidates = sorted(
            (root.glob(f"*/bin/{name}.exe") for root in roots),
            key=lambda _x: 0,
        )
        for group in candidates:
            found_path = next(iter(group), None)
            if found_path:
                return str(found_path)
    raise BackupError(f"Required PostgreSQL utility is missing: {name}")


def _pgpass(settings: dict[str, str]):
    def escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:")

    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, prefix="exesoft-pgpass-")
    try:
        values = (settings["host"], settings["port"], "*", settings["user"], settings["password"])
        handle.write(":".join(escape(value) for value in values) + "\n")
        handle.close()
        try:
            os.chmod(handle.name, 0o600)
        except OSError:
            # On Windows the file is created under the current user's temp ACL.
            pass
        return Path(handle.name)
    except Exception:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)
        raise


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise BackupError(f"Could not execute PostgreSQL utility: {Path(args[0]).name}") from exc
    if result.returncode:
        # Native tools can include host/database identifiers, but do not relay raw
        # command output because libpq errors may contain connection details.
        raise BackupError(f"PostgreSQL utility failed: {Path(args[0]).name} (exit {result.returncode})")
    return result


def _tool_env(passfile: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PGPASSFILE"] = str(passfile)
    env["PGCONNECT_TIMEOUT"] = env.get("PGCONNECT_TIMEOUT", "10")
    return env


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def backup(destination: Path = DEFAULT_BACKUP_DIR, retention_days: int = 30) -> Path:
    if retention_days < 1:
        raise BackupError("Retention must be at least one day")
    settings = connection_settings()
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        destination.chmod(0o700)
    except OSError:
        pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"exesoft_{settings['database']}_{stamp}_{uuid.uuid4().hex[:8]}"
    archive = destination / f"{stem}.dump"
    temp_archive = destination / f".{stem}.partial"
    manifest = destination / f"{stem}.manifest.json"
    passfile = _pgpass(settings)
    try:
        _run([
            _tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges",
            "--host", settings["host"], "--port", settings["port"],
            "--username", settings["user"], "--dbname", settings["database"],
            "--file", str(temp_archive),
        ], _tool_env(passfile))
        if not temp_archive.is_file() or temp_archive.stat().st_size == 0:
            raise BackupError("pg_dump returned without producing a valid archive")
        digest = _digest(temp_archive)
        temp_archive.replace(archive)
        metadata = {
            "format": "postgresql-custom",
            "database": settings["database"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "archive": archive.name,
            "size_bytes": archive.stat().st_size,
            "sha256": digest,
        }
        manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        manifest.chmod(0o600)
    except Exception:
        temp_archive.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise
    finally:
        passfile.unlink(missing_ok=True)
    prune(destination, retention_days, now=datetime.now(timezone.utc))
    return archive


def verify(archive: Path) -> dict:
    archive = archive.expanduser().resolve(strict=True)
    if not BACKUP_RE.fullmatch(archive.name):
        raise BackupError("Archive filename is not a recognized EXESOFT backup")
    manifest_path = archive.with_name(archive.name.removesuffix(".dump") + ".manifest.json")
    try:
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError("Backup manifest is missing or invalid") from exc
    if metadata.get("archive") != archive.name or metadata.get("format") != "postgresql-custom":
        raise BackupError("Backup manifest does not match archive")
    if metadata.get("size_bytes") != archive.stat().st_size or metadata.get("sha256") != _digest(archive):
        raise BackupError("Backup integrity check failed")
    listing = _run([_tool("pg_restore"), "--list", str(archive)], os.environ.copy())
    if not listing.stdout.strip():
        raise BackupError("PostgreSQL archive has an empty table of contents")
    return metadata


def prune(directory: Path, retention_days: int, now: datetime | None = None) -> list[str]:
    """Remove only expired, well-formed backups in the configured directory."""
    if retention_days < 1:
        raise BackupError("Retention must be at least one day")
    directory = directory.resolve(strict=True)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    removed: list[str] = []
    for archive in directory.iterdir():
        if archive.is_symlink() or not archive.is_file() or not BACKUP_RE.fullmatch(archive.name):
            continue
        manifest = archive.with_name(archive.name.removesuffix(".dump") + ".manifest.json")
        if not manifest.is_file() or manifest.is_symlink():
            continue
        modified = datetime.fromtimestamp(archive.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            archive.unlink()
            manifest.unlink()
            removed.append(archive.name)
    return removed


def _assert_existing_restore_target(settings: dict[str, str], target_database: str) -> None:
    """Require an existing, empty database owned by the configured DB user."""
    try:
        with psycopg.connect(
            host=settings["host"], port=settings["port"], dbname=target_database,
            user=settings["user"], password=settings["password"], connect_timeout=10,
            options="-c default_transaction_read_only=on",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT current_database(), current_user,
                           pg_get_userbyid(d.datdba),
                           has_database_privilege(current_user, current_database(), 'CREATE'),
                           EXISTS (
                             SELECT 1 FROM pg_catalog.pg_namespace n
                             WHERE n.nspname NOT IN (
                               'pg_catalog', 'information_schema', 'public', 'pg_toast'
                             )
                           ) OR EXISTS (
                             SELECT 1 FROM pg_catalog.pg_class c
                             JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                             WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                               AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                           ) OR EXISTS (
                             SELECT 1 FROM pg_catalog.pg_proc p
                             JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
                             WHERE n.nspname = 'public'
                           ) OR EXISTS (
                             SELECT 1 FROM pg_catalog.pg_type t
                             JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
                             WHERE n.nspname = 'public' AND t.typtype <> 'p'
                           ) OR EXISTS (
                             SELECT 1 FROM pg_catalog.pg_extension e
                             WHERE e.extname <> 'plpgsql'
                           )
                    FROM pg_catalog.pg_database d
                    WHERE d.datname = current_database()
                """)
                row = cur.fetchone()
    except psycopg.Error as exc:
        raise BackupError("Restore target is not reachable with the configured database account") from exc
    if not row or row[0] != target_database:
        raise BackupError("Restore connection did not select the exact target database")
    if row[1] != row[2]:
        raise BackupError("Configured database account must own the pre-provisioned restore database")
    if not row[3]:
        raise BackupError("Configured database account lacks CREATE on the restore database")
    if row[4]:
        raise BackupError("Restore target is not empty; refusing to alter existing objects")


def restore_to_existing_database(
    archive: Path, target_database: str, *, confirmed_target: str | None = None
) -> None:
    metadata = verify(archive)
    settings = connection_settings()
    if not RESTORE_TARGET_RE.fullmatch(target_database):
        raise BackupError("Restore target must use the exesoft_restore_ prefix")
    if confirmed_target != target_database:
        raise BackupError("Restore target requires an exact operator provenance confirmation")
    if (target_database.casefold() == "exesoft_factory"
            or target_database == settings["database"]
            or target_database == metadata.get("database")):
        raise BackupError("Restore target must not be the source or configured production database")
    _assert_existing_restore_target(settings, target_database)
    passfile = _pgpass(settings)
    env = _tool_env(passfile)
    try:
        _run([
            _tool("pg_restore"), "--exit-on-error", "--no-owner", "--no-privileges",
            "--host", settings["host"], "--port", settings["port"],
            "--username", settings["user"], "--dbname", target_database, str(archive.resolve()),
        ], env)
    finally:
        passfile.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("backup", help="Create and verify a consistent custom-format backup")
    make.add_argument("--directory", type=Path, default=DEFAULT_BACKUP_DIR)
    make.add_argument("--retention-days", type=int, default=30)
    check = sub.add_parser("verify", help="Check SHA-256, manifest, and PostgreSQL archive TOC")
    check.add_argument("archive", type=Path)
    restore = sub.add_parser("restore", help="Restore to a pre-provisioned empty exesoft_restore_* database")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--target-database", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            archive = backup(args.directory, args.retention_days)
            verify(archive)
            print(f"Backup verified: {archive.name}")
        elif args.command == "verify":
            metadata = verify(args.archive)
            print(f"Backup verified: {metadata['archive']} ({metadata['size_bytes']} bytes)")
        else:
            confirmed = os.environ.get("EXESOFT_BACKUP_RESTORE_CONFIRMED")
            restore_to_existing_database(
                args.archive, args.target_database, confirmed_target=confirmed
            )
            print(f"Restore completed in isolated database: {args.target_database}")
        return 0
    except BackupError as exc:
        print(f"Backup operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
