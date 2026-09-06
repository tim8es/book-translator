# Workflow v2 Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit, crash-recoverable Workflow v2 upgrades from provable legacy v0/v1 book state to the currently installed workflow revision without silent provenance or review/source fabrication.

**Architecture:** A backend-neutral `migrations.py` performs raw-state discovery, pure v0→v1 migration, compatibility planning, journaling and CAS recovery. A thin CLI adapter resolves installed provenance and exposes `workflow-upgrade`; the existing coordination/status/claim/finalize/review boundaries are extended only enough to make an active migration journal an authoritative recovery gate.

**Tech Stack:** Python 3.10+, stdlib (`json`, `base64`, `hashlib`, `datetime`, `copy`), existing `StorageBackend`, `WorkflowStateRepository`, schema/review/source/coordination primitives, `unittest`.

**Spec:** `docs/WORKFLOW_V2_MIGRATIONS_DESIGN.md`

## Global Constraints

- No silent durable migration outside explicit `workflow-upgrade`.
- Missing `schema_version` is logical v0 only in the migration path.
- `--to` must exactly equal installed `.book-translator-install.json` `resolved_revision`.
- Never invent claim/review/source provenance.
- Metadata workflow pin is written last.
- `.workflow/migration.json` is authoritative while present.
- Unknown concurrent mutation during recovery is never overwritten.
- No third-party dependency, database, queue or GitHub Actions runtime requirement.
- PR target is `refactor/workflow-engine-v2`; `main` remains unchanged.

---

### Task 1: Pure migration registry and journal schema

**Files:**
- Create: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Test: `tests/test_workflow_v2_migrations.py`

**Interfaces:**
- Produces: `MigrationError`, `MigrationCompatibilityError`, `MigrationConflict`, `detect_schema_version(data) -> int`, `migrate_document(kind, data) -> MigratedDocument`.
- Produces schema: `SchemaKind.MIGRATION_JOURNAL` with strict v1 validator.
- `MigratedDocument` fields: `kind`, `from_version`, `to_version`, `data`, `changed`.

- [ ] **Step 1: Write failing registry tests**

Add tests that require:

```python
legacy = valid_metadata()
legacy.pop("schema_version")
result = migrate_document(SchemaKind.METADATA, legacy)
self.assertEqual(result.from_version, 0)
self.assertEqual(result.to_version, 1)
self.assertEqual(result.data["schema_version"], 1)
self.assertTrue(result.changed)

current = migrate_document(SchemaKind.PROGRESS, valid_progress())
self.assertEqual(current.from_version, 1)
self.assertFalse(current.changed)

with self.assertRaises(MigrationCompatibilityError):
    migrate_document(SchemaKind.CLAIM, {"claim_id": "missing-everything"})

future = valid_metadata()
future["schema_version"] = 2
with self.assertRaises(MigrationCompatibilityError):
    migrate_document(SchemaKind.METADATA, future)
```

Also require `MIGRATION_JOURNAL` to accept a valid prepared journal and reject unsafe paths, invalid SHA-256/base64/original-null combinations, unsupported phases and missing document fields.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest tests.test_workflow_v2_migrations -v`

Expected: assertion-level failures because migration API / journal schema do not exist; existing suite remains importable.

- [ ] **Step 3: Implement minimal pure registry and journal validator**

`migrations.py` must implement:

```python
@dataclass(frozen=True)
class MigratedDocument:
    kind: SchemaKind
    from_version: int
    to_version: int
    data: dict[str, Any]
    changed: bool


def detect_schema_version(data: Mapping[str, Any]) -> int:
    if "schema_version" not in data:
        return 0
    value = data["schema_version"]
    if type(value) is not int:
        raise MigrationCompatibilityError("schema_version must be an integer")
    return value


def migrate_document(kind: SchemaKind, data: Mapping[str, Any]) -> MigratedDocument:
    version = detect_schema_version(data)
    if version == 1:
        parsed = parse_document(kind, data)
        return MigratedDocument(kind, 1, 1, parsed.data, False)
    if version != 0:
        raise MigrationCompatibilityError(...)
    candidate = copy.deepcopy(dict(data))
    candidate["schema_version"] = 1
    try:
        parsed = parse_document(kind, candidate)
    except SchemaError as exc:
        raise MigrationCompatibilityError(f"{kind.value} v0 is not v1-compatible: {exc}") from exc
    return MigratedDocument(kind, 0, 1, parsed.data, True)
```

`schemas.py` adds `MIGRATION_JOURNAL`, validates phase `prepared|applied`, safe relative paths, original/target hashes, base64 round-trip shape, and resulting revision null/string.

- [ ] **Step 4: Run focused + schema regression tests**

Run:
`python -m unittest tests.test_workflow_v2_migrations tests.test_workflow_v2_schemas -v`

Expected: GREEN.

- [ ] **Step 5: Commit Task 1**

Commit message: `feat: add workflow migration registry and journal schema`

---

### Task 2: Compatibility planner for provenance, source, review and claims

**Files:**
- Modify: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/source_integrity.py`
- Test: `tests/test_workflow_v2_migration_planner.py`

**Interfaces:**
- Produces `MigrationPlan` with `book_slug`, `from_revision`, `to_revision`, `documents`, `lifecycle_downgrades`, `changed`.
- Produces `PlannedWrite` with `path`, `kind`, `original_exists`, `original_version`, `original_bytes`, `target_bytes`, `from_version`, `to_version`.
- Produces `build_private_source_manifest_from_identity(book_dir, metadata, progress) -> dict[str, Any]` in source integrity module.
- Consumes installed provenance mapping `{canonical_repository, requested_ref, resolved_revision}` and injected `now`.

- [ ] **Step 1: Write planner RED tests**

Cover:

```python
plan = planner.plan(slug="legacy", to_revision="new-rev", installed=installed)
self.assertEqual(plan.from_revision, "old-rev")
self.assertEqual(plan.to_revision, "new-rev")
self.assertTrue(plan.changed)
self.assertEqual(plan.target_metadata["workflow"]["resolved_revision"], "new-rev")
self.assertEqual(plan.target_metadata["workflow"]["review_evidence"], "review-ledger-v1")
```

Required scenarios:

- target differs from installed revision → compatibility error, no writes;
- malformed/future schema → precise error, no writes;
- missing ledger → candidate empty v1 ledger;
- reviewed without current PASS but valid translation → candidate progress `translated` + downgrade record;
- reviewed with current PASS remains reviewed;
- missing/empty translation for reviewed → compatibility error;
- missing embedded manifest reconstructed from exact source/extracted bytes;
- private-external manifest reconstructed from metadata source identity + extracted hashes while original binary is absent;
- missing unprovable source identity/artifact → compatibility error;
- live claim blocks; deterministic expired claim is allowed/migrated without lease extension;
- active finalization marker blocks;
- true current v1/current-revision workspace returns `changed=False` and no writes.

- [ ] **Step 2: Verify planner RED**

Run: `python -m unittest tests.test_workflow_v2_migration_planner -v`

Expected: failures only for missing planner/source reconstruction APIs.

- [ ] **Step 3: Implement raw discovery and pure candidate planning**

Use `repository.storage.read/list` for raw legacy JSON. Decode UTF-8/JSON strictly and preserve exact bytes/version. Do not write.

Planner algorithm:

```python
raw = discover_state()
verify_installed_target(installed, to_revision)
migrated = migrate_supported_documents(raw)
ledger = existing_or_empty_v1_ledger(...)
progress = reconcile_reviewed_lifecycle(...)
manifest = existing_or_reconstructed_manifest(...)
validate_cross_document_candidate(...)
metadata = with_target_workflow_and_history_last(...)
return build_plan(raw, candidates)
```

For private source reconstruction, add a helper that uses metadata's already-proven `source.sha256/size_bytes/storage_mode` and exact extracted file hashes; it must not require source binary bytes.

- [ ] **Step 4: Run focused planner/source tests**

Run:
`python -m unittest tests.test_workflow_v2_migration_planner tests.test_workflow_v2_private_source tests.test_workflow_v2_corpus_manifest -v`

Expected: GREEN.

- [ ] **Step 5: Commit Task 2**

Commit message: `feat: plan explicit workflow upgrades`

---

### Task 3: Coordination and transaction executor

**Files:**
- Modify: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/workflow_v2/coordination.py`
- Test: `tests/test_workflow_v2_migration_transaction.py`
- Test: `tests/test_workflow_v2_coordination.py`

**Interfaces:**
- `BookCoordinationManager.acquire(operation="workflow_upgrade", ...)` becomes valid.
- Produces `MigrationExecutor.execute(plan, *, session_id) -> MigrationResult`.
- Produces `MigrationExecutor.recover(*, session_id) -> MigrationResult | None`.
- `MigrationResult`: `book_slug`, `from_revision`, `to_revision`, `outcome` (`changed|unchanged|recovered`), `migrated_paths`, `lifecycle_downgrades`.

- [ ] **Step 1: Write transaction RED tests**

Use an instrumented storage backend to verify:

- journal is created before target writes;
- write order source manifest → review ledger → sorted claims → progress → metadata;
- metadata target is last;
- every existing write uses captured CAS revision;
- journal revision is CAS-updated after each successful target write;
- successful final validation deletes journal;
- stale target CAS triggers exact rollback and leaves original bytes byte-identical;
- path originally absent is deleted during rollback;
- coordination operation `workflow_upgrade` is accepted and released.

- [ ] **Step 2: Verify RED**

Run:
`python -m unittest tests.test_workflow_v2_migration_transaction tests.test_workflow_v2_coordination -v`

Expected: transaction/operation failures only.

- [ ] **Step 3: Implement deterministic executor**

Journal entry creation must store exact original bytes as base64 and hashes. Apply target bytes through raw storage CAS/create operations so canonical bytes from `WorkflowStateRepository.serialize()` are preserved.

On apply exception:

```python
try:
    apply_targets(plan, journal)
    validate_durable_target(plan)
except Exception as exc:
    rollback_known_states(journal)
    raise MigrationConflict(...) from exc
```

Rollback writes exact decoded original bytes, not reparsed documents.

- [ ] **Step 4: Implement recovery classifier**

For each journal entry classify current path as `original`, `target`, or `unknown` by SHA-256/absence. Unknown must raise `MigrationConflict` without mutation. Mixed original/target rolls back, deletes journal, replans and executes. All target validates and removes journal as `recovered`.

- [ ] **Step 5: Run focused transaction tests**

Run:
`python -m unittest tests.test_workflow_v2_migration_transaction tests.test_workflow_v2_coordination -v`

Expected: GREEN.

- [ ] **Step 6: Commit Task 3**

Commit message: `feat: add crash-recoverable workflow upgrade transaction`

---

### Task 4: Active migration admission and fresh-session recovery visibility

**Files:**
- Modify: `scripts/workflow_v2/coordination.py`
- Modify: `scripts/workflow_v2/claims.py`
- Modify: `scripts/workflow_v2/finalize.py`
- Modify: `scripts/workflow_v2/reviews.py`
- Modify: `scripts/workflow_v2/status.py`
- Test: `tests/test_workflow_v2_migration_visibility.py`

**Interfaces:**
- `BookCoordinationManager.migration_active() -> bool` reads `.workflow/migration.json` strictly.
- Status snapshot adds `migration` section with active/phase/from/to.
- Resume returns `operation="workflow_upgrade"` before normal claim/lifecycle dispatch when journal exists.

- [ ] **Step 1: Write visibility/admission RED tests**

Require:

```python
self.assertRaises(ClaimError, claim_manager.acquire, ...)
self.assertRaises(FinalizationError, finalizer.finalize, ...)
self.assertRaises(ReviewEvidenceError, review_manager.accept_review, ...)
status = resolver.status(...)
self.assertTrue(status["migration"]["active"])
resume = resolver.resume(...)
self.assertEqual(resume["operation"], "workflow_upgrade")
```

Also malformed migration journal must invalidate status/fail closed rather than be ignored.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_workflow_v2_migration_visibility -v`

Expected: failures because existing admission/status paths do not know migration journal.

- [ ] **Step 3: Add minimal guards/status routing**

Do not duplicate migration logic. All guards call `coordination.migration_active()`. Status reads the journal through repository/schema API and emits bounded recovery context only.

- [ ] **Step 4: Run visibility plus existing claim/finalize/review/status tests**

Run:
`python -m unittest tests.test_workflow_v2_migration_visibility tests.test_workflow_v2_claims tests.test_workflow_v2_finalize tests.test_workflow_v2_reviews tests.test_workflow_v2_status -v`

Expected: GREEN.

- [ ] **Step 5: Commit Task 4**

Commit message: `feat: gate workflow operations during migration recovery`

---

### Task 5: CLI workflow-upgrade

**Files:**
- Create: `scripts/workflow_v2/migrations_cli.py`
- Modify: `scripts/workflow_v2/review_cli.py` (registration chain only, following existing finalize/EPUB adapter pattern)
- Test: `tests/test_workflow_v2_migrations_cli.py`

**Interfaces:**
- Register `book.py workflow-upgrade <slug> --to <revision> [--json]`.
- Resolve installed provenance from root `.book-translator-install.json`.
- Convert expected migration/storage/schema/coordination failures to `ReviewCliError` boundary or a migration adapter error already caught by the registration chain.

- [ ] **Step 1: Write CLI RED tests**

Create temp workspaces with copied scripts and explicit install provenance. Cover:

- explicit legacy metadata/progress source migrates and records old/new revision;
- no command invocation means byte-identical legacy files;
- `--to` mismatch fails before writes;
- malformed legacy fixture fails with concise error/no traceback/no mutation;
- reviewed-without-ledger downgrades to translated;
- private-external upgrade succeeds without source binary;
- second identical upgrade is `changed=false` / byte-idempotent;
- `--json` output is canonical/deterministic apart from no wall-clock data.

- [ ] **Step 2: Verify CLI RED**

Run: `python -m unittest tests.test_workflow_v2_migrations_cli -v`

Expected: parser reports no `workflow-upgrade` command.

- [ ] **Step 3: Implement thin CLI adapter and registration**

Adapter flow:

```python
installed = load_install_provenance(root)
executor = build_filesystem_migration_executor(book_dir, installed=installed)
result = executor.recover(session_id=...) or executor.execute(
    executor.plan(slug, args.to, installed),
    session_id=...,
)
print_result(result, json_mode=args.json)
```

Use a deterministic CLI session identifier; migration correctness must not depend on random CLI output fields.

- [ ] **Step 4: Run CLI + parser regression tests**

Run:
`python -m unittest tests.test_workflow_v2_migrations_cli tests.test_book_cli tests.test_workflow_v2_finalize_cli tests.test_workflow_v2_epub_cli -v`

Expected: GREEN.

- [ ] **Step 5: Commit Task 5**

Commit message: `feat: add explicit workflow upgrade command`

---

### Task 6: #18 migration reliability and idempotence

**Files:**
- Create: `tests/test_workflow_v2_migration_reliability.py`
- Modify production only if a test demonstrates a real invariant defect.

**Interfaces:** uses public migration/executor/CLI APIs from Tasks 1–5.

- [ ] **Step 1: Add failure-injection scenarios without production fault hooks**

Construct exact durable boundaries directly with real filesystem storage/journal:

- crash after journal creation before target write → fresh CLI recovery completes safely;
- crash after review-ledger/source write before progress/metadata → fresh recovery restores/retries and succeeds;
- crash after metadata target before journal deletion → fresh recovery returns recovered and does not duplicate history;
- stale CAS/concurrent known winner → original valid state preserved;
- unknown mutation while journal active → fresh recovery fails closed and preserves unknown bytes/journal;
- identical completed rerun causes no durable byte changes.

- [ ] **Step 2: Run reliability tests**

Run: `python -m unittest tests.test_workflow_v2_migration_reliability -v`

Expected: GREEN if implementation already satisfies invariants. If a genuine defect is exposed, preserve RED evidence, make the smallest owning-component fix, and rerun to GREEN.

- [ ] **Step 3: Commit reliability coverage/fixes**

Commit test-only coverage separately from any real production defect fix.

---

### Task 7: Full verification and integration audit

**Files:** no planned production changes.

- [ ] **Step 1: Run full suite**

Run: `python -m unittest discover -s tests -v`

Required CI matrix: Python 3.10 and Python 3.12, both success on exact final head.

- [ ] **Step 2: Audit acceptance requirements**

Verify every spec acceptance criterion has a named passing test, especially no-silent-write, target provenance, review downgrade, private source, migration visibility, rollback and unknown mutation.

- [ ] **Step 3: Audit diff/ancestry/reviews**

Require:

- PR base `refactor/workflow-engine-v2`;
- feature `behind_by=0` and merge-base equals branch creation integration SHA;
- only #16 design/plan/migration/admission/test files changed;
- no unresolved review threads/comments/request-changes;
- `main` still at its pre-task SHA.

- [ ] **Step 4: Ready and guarded merge**

Update PR body with exact RED/GREEN/final CI evidence. Mark Ready only after all guards are clean. Merge only into `refactor/workflow-engine-v2` with expected-head SHA. Preserve feature branch. Do not merge to `main`.
