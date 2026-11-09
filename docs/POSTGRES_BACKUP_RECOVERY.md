# PostgreSQL backup and recovery

EXESOFT uses PostgreSQL's native `pg_dump` custom archive format. `pg_dump`
captures a transactionally consistent snapshot while writes continue. The
archive is accompanied by a JSON manifest with its database name, size,
creation time, and SHA-256 digest. Connection credentials are read from the existing
`POSTGRES_MIGRATOR_*`, `POSTGRES_HOST`, `POSTGRES_PORT`, and `POSTGRES_DB`
settings; a short-lived, access-restricted libpq password file is removed after
each operation. Connection passwords are never placed on a command line, in
backup metadata, or in output logs. A database backup includes its contents, so
production archives may contain application-stored secrets; the F7-003 round
trip uses a synthetic-only fixture.

Keep `backups/postgres` on storage restricted to the database operators and
protect off-host copies with the organization's encryption and access controls.
The backup command applies a 30-day default retention policy and only removes
expired, well-formed EXESOFT archive/manifest pairs from that directory. Set a
different retention window explicitly where policy requires it.

## Existing F7-003 validation evidence

F7-003 was approved by the operator. The checked-in live test validates
source/restore identities, migration versions 000–017, synthetic-only fixture
markers, archive SHA-256 and TOC, schema signature, row counts and fixture
content equality. The test and this procedure are retained as the reproducible
evidence; no second restore was performed during the F8-004 audit. The original
operator test transcript/result artifact is not stored in this repository, so
its exact live-test counts cannot be independently reproduced from checked-in
evidence alone. Both F7-003 databases remain outside the current test workflow.

```powershell
& 'C:\AI-Factory\.venv\Scripts\python.exe' tools\postgres_backup.py backup --retention-days 30
& 'C:\AI-Factory\.venv\Scripts\python.exe' tools\postgres_backup.py verify .\backups\postgres\<archive>.dump
```

Verification checks the sidecar manifest, file size, SHA-256, and PostgreSQL
archive table of contents. Restore only targets an existing, empty
`exesoft_restore_*` database. The configured account must own that database and
have CREATE on it. The tool verifies the exact target, refuses the source and
production database, requires an exact target-name confirmation, and never
creates or drops a database. It does not use `--clean`; any conflicting object
causes the native restore to fail. The restored database is retained for review.

```powershell
 $env:EXESOFT_BACKUP_RESTORE_CONFIRMED = 'exesoft_restore_drill_20261105'
 & 'C:\AI-Factory\.venv\Scripts\python.exe' tools\postgres_backup.py restore .\backups\postgres\<archive>.dump `
   --target-database exesoft_restore_drill_20261105
```

## F7-003 isolated round-trip

Database lifecycle is performed by an authorized operator outside the test.
Do not run the following provisioning commands if either named database already
exists. They use an existing database provisioner interactively (`--password`)
and assign ownership to the existing `POSTGRES_MIGRATOR_USER` from `.env`; they
do not create or alter roles. The provisioner needs CREATEDB, but need not be a
superuser.

```powershell
$dbProvisioner = Read-Host 'Existing authorized database provisioner role'
& 'C:\Program Files\PostgreSQL\18\bin\createdb.exe' --host 127.0.0.1 --port 5432 --username $dbProvisioner --password --template template0 --owner exesoft_factory_migrator exesoft_backup_fixture_f7003_20261105
if ($LASTEXITCODE -ne 0) { throw 'Fixture creation failed; stop.' }
& 'C:\Program Files\PostgreSQL\18\bin\createdb.exe' --host 127.0.0.1 --port 5432 --username $dbProvisioner --password --template template0 --owner exesoft_factory_migrator exesoft_restore_f7003_20261105
if ($LASTEXITCODE -ne 0) { throw 'Restore database creation failed; stop.' }
```

Set `POSTGRES_DB` to the exact source fixture and run
`C:\AI-Factory\.venv\Scripts\python.exe tools\prepare_f7003_source.py`. The helper verifies `current_database()`,
checks that the configured migration account is a non-superuser owner of the
fixture, and applies the repository's migration engine set 000–017 only there.
It never connects the migration engine to `exesoft_factory`. The target database
must remain empty and owned by the configured migration account. The test uses
only that account from `.env`; it needs no CREATEDB privilege. It inserts one
synthetic tenant/project through the Runtime, checks the migration history,
backup SHA-256/TOC, schema signature, all user-table row counts, and the
synthetic project's request and tenant values. Neither database is dropped by
the test. Review or remove these dedicated fixtures separately using an
authorized database operator.

```powershell
$env:POSTGRES_DB = 'exesoft_backup_fixture_f7003_20261105'
$env:EXESOFT_BACKUP_TEST_SOURCE_CONFIRMED = $env:POSTGRES_DB
& 'C:\AI-Factory\.venv\Scripts\python.exe' tools\prepare_f7003_source.py
if ($LASTEXITCODE -ne 0) { throw 'Fixture preparation failed; stop.' }
$env:EXESOFT_BACKUP_TEST_ENABLED = '1'
$env:EXESOFT_BACKUP_TEST_SOURCE_DB = 'exesoft_backup_fixture_f7003_20261105'
$env:EXESOFT_BACKUP_TEST_RESTORE_DB = 'exesoft_restore_f7003_20261105'
$env:EXESOFT_BACKUP_TEST_RESTORE_CONFIRMED = $env:EXESOFT_BACKUP_TEST_RESTORE_DB
$base = Join-Path $PWD ('.pytest-tmp-' + [guid]::NewGuid().ToString('N'))
& 'C:\AI-Factory\.venv\Scripts\python.exe' -m pytest -p no:cacheprovider --basetemp $base tests/test_f7003_postgres_backup.py::test_native_backup_restore_roundtrip_in_isolated_database -q
$testExit = $LASTEXITCODE
if (Test-Path -LiteralPath $base) { Remove-Item -LiteralPath $base -Recurse -Force }
$env:POSTGRES_DB = $null
$env:EXESOFT_BACKUP_TEST_ENABLED = $null
$env:EXESOFT_BACKUP_TEST_SOURCE_DB = $null
$env:EXESOFT_BACKUP_TEST_SOURCE_CONFIRMED = $null
$env:EXESOFT_BACKUP_TEST_RESTORE_DB = $null
$env:EXESOFT_BACKUP_TEST_RESTORE_CONFIRMED = $null
if ($testExit -ne 0) { throw "F7-003 round-trip failed ($testExit); preserve both databases for review." }
```

Before provisioning, an operator should confirm the exact database names do not
exist. Existing names with unknown provenance must be left untouched. The
application and test never execute CREATE DATABASE or DROP DATABASE.

Point `PG_DUMP` or `PG_RESTORE` at version-matched native PostgreSQL executables
if they are not available on `PATH`.
