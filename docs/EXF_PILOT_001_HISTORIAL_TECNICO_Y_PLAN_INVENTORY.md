# EXF Pilot 001 — Technical history and Inventory pilot

Date: 2026-11-08  
Repository: EXESOFT AI FACTORY  
Status: Factory roadmap work F0–F8 completed; Pilot 001 planning-contract work is implemented and regression-tested. EXESOFT Inventory remains unplanned and unexecuted.

## Purpose and boundaries

This document records the Factory infrastructure and planning-contract work accumulated since the prior repository checkpoint, plus the controlled state of the EXESOFT Inventory pilot. It is a technical record, not an Inventory implementation plan. No Inventory source code, generated plan, task execution, model inference, or Sandy workload is included in this checkpoint.

Production PostgreSQL remains `exesoft_factory`. PostgreSQL integration tests were directed only to `exesoft_test_f7004`, with both configured and connected database identity checked before integration writes. The F7-003 backup/restore fixture databases are separate and were not used by the planning-contract regression run. A Git checkpoint is not a replacement for native PostgreSQL backups.

## Factory delivery history

The repository now contains the delivered F0–F8 Factory layers and associated tests and documentation:

- **F0–F3 — foundations and PostgreSQL persistence:** repository and UnitOfWork persistence, migrations through version 017, consistency checks, and test isolation guards. Applied migrations 000–017 are existing history; this planning-contract work adds no migration.
- **F4 — durable Runtime and Orchestrator:** PostgreSQL-backed execution, atomic task claims, recovery, and correlated lifecycle events.
- **F5 — model access and coordination:** provider-independent Model Gateway with routing/fallback and persisted agent handoffs.
- **F6 — isolated execution and evidence:** Sandy integration, durable artifacts, Quality Gate enforcement, and pipeline regression coverage.
- **F7 — operational interfaces and support:** CLI, API, PostgreSQL backup/restore procedures, and structured observability.
- **F8 — integrated validation:** end-to-end, concurrency/isolation, recovery/restart, and final audit test coverage.

The detailed implementation is represented by the code, migrations, operational documentation, and test modules in this repository. Historical test counts quoted in earlier task reports are point-in-time results; the current planning-contract regression evidence is recorded below.

## Planning contract `exf.plan.v1`

Requirements-driven planning uses a single versioned contract in `engine/planning_contract.py`. The Orchestrator includes its schema and an explicit Factory-owned requirement catalog in the Gateway prompt. Requirement IDs are stable hashes of normalized requirement text. The model supplies local references for specs and tasks; Factory resolves them deterministically to its own IDs.

The contract validates schema version and fields, local references, allowed agents, structured acceptance criteria, dependency references and acyclicity, and requirement-to-spec coverage. Coverage is many-to-many: duplicate edges and unknown IDs are rejected, and each authoritative requirement must have at least one valid edge. Diagnostics expose paths and failure categories without echoing generated content. Only safe structural parsing is normalized. A failed draft may receive one bounded repair request with sanitized diagnostics; the repaired response is validated from scratch. Invalid plans do not reach persistence.

Acceptance criteria retain `description`, a concrete `verification` procedure, and an observable `expected_result`. These fields remain available through ODD persistence and execution context. Historical ODD free-form criteria remain readable; they are not accepted as criteria in a new `exf.plan.v1` plan.

Validated plans continue through the existing PostgreSQL persistence path and T3 UnitOfWork. Plan writes remain atomic, idempotent, serialized per tenant/project, and versioned. Approval remains explicit and tied to the plan fingerprint; planning never approves a plan, and execution remains guarded by T1 approval enforcement. No production schema change was required.

## EXESOFT Inventory pilot state

The approved product is a local-first inventory web application. The EXESOFT tenant and an `EXESOFT Inventory` project record were created through the existing Factory persistence interface in the production database under separate operator authorization. The project remains in its initial `created` state. This work did not plan or execute it, did not change its records, and did not generate application code. A future plan requires a separate explicit operator action through the Factory planner and remains subject to the plan approval guard before execution.

## Verification evidence

The final relevant planning and F3–F8 regression selection was run with the repository Python resolver and the isolated PostgreSQL test database. Before tests, both configuration and `current_database()` were verified as `exesoft_test_f7004`; live backup testing was disabled for this run.

Result: **163 passed, 0 failed, 0 errors, 1 skipped**. The skipped case is the live backup test, intentionally disabled for this regression. CLI/API integration subset: **9 passed**. Gateway calls were mocked; no real model inference, agent execution, or Sandy workload was performed.

## Operational safeguards

- Keep database passwords and model keys in environment/secret configuration; never commit `.env`, credential stores, logs containing secrets, database files, or runtime artifacts.
- Before any PostgreSQL integration suite, set both `POSTGRES_DB` and `EXESOFT_TEST_POSTGRES_DB` to `exesoft_test_f7004`, set `EXESOFT_BACKUP_TEST_ENABLED=0`, and verify the live `current_database()` identity. Tests fail closed on mismatch.
- Do not apply migrations as part of ordinary test execution. Any new migration requires separate review and operator approval before application.
- Keep Sandy isolation and Quality Gate enforcement enabled. Do not use real or paid provider calls in deterministic regression tests.
- Preserve the F7-003 source and restore fixtures; do not use them as general test databases.

## Scope of this checkpoint

This record accompanies reviewed Factory infrastructure, tests, and operational documentation. It does not assert that every local working-tree file is safe to commit; the checkpoint must stage only the reviewed allowlist. Temporary pytest output, patch archives, local credentials, databases, and generated execution artifacts are excluded.
