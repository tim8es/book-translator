# Workflow v2 Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit, crash-recoverable Workflow v2 upgrades from provable legacy v0/v1 state to the currently installed workflow revision without silent provenance or review/source fabrication.

**Architecture:** `MigrationPlanner` performs raw discovery and builds a fully validated immutable `MigrationPlan`; `MigrationExecutor` alone owns coordination, journaling, CAS application, rollback and recovery. A thin CLI adapter resolves installed provenance and exposes `workflow-upgrade`; existing claim/finalize/review/status boundaries only learn how to block or route around an active migration journal.

**Tech Stack:** Python 3.10+, stdlib (`json`, `base64`, `hashlib`, `datetime`, `copy`), existing `StorageBackend`, `WorkflowStateRepository`, schema/review/source/coordination primitives, `unittest`.

**Spec:** `docs/WORKFLOW_V2_MIGRATIONS_DESIGN.md`

## Global Constraints

- No silent durable migration outside explicit `workflow-upgrade`.
- Missing `schema_version` is logical v0 only in migration code.
- `--to` must exactly equal installed `.book-translator-install.json` `resolved_revision`.
- Never invent claim/review/source provenance.
- Metadata workflow pin is written last.
- `.workflow/migration.json` is authoritative while present.
- Unknown concurrent mutation is never overwritten.
- No new third-party dependency or mandatory GitHub Actions runtime.
- Merge only into `refactor/workflow-engine-v2`; `main` unchanged.

---

### Task 1: Pure registry + migration journal schema

**Files:**
- Create: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Test: `tests/test_workflow_v2_migrations.py`

**Interfaces:**

```python
class MigrationError(RuntimeError): ...
class MigrationCompatibilityError(MigrationError): ...
class MigrationConflict(MigrationError): ...

@dataclass(frozen=True)
class MigratedDocument:
    kind: SchemaKind
    from_version: int
    to_version: int
    data: dict[str, Any]
    changed: bool

def detect_schema_version(data: Mapping[str, Any]) -> int: ...
def migrate_document(kind: SchemaKind, data: Mapping[str, Any]) -> MigratedDocument: ...
```

Also add `SchemaKind.MIGRATION_JOURNAL`.

- [ ] **Step 1 — RED tests:** require v0 metadata/progress/review-ledger/claim/source-manifest to become strict v1 after only adding `schema_version`; strict v1 is unchanged; version 2 and incomplete v0 fail with `MigrationCompatibilityError`. Require valid migration journal acceptance and invalid phase/path/hash/base64/null combinations rejection.
- [ ] **Step 2 — Run RED:** `python -m unittest tests.test_workflow_v2_migrations -v`; expected assertion-level failures for missing migration API/schema only.
- [ ] **Step 3 — Minimal implementation:** deep-copy, detect 0/1, call existing `parse_document`; no defaults beyond `schema_version: 1`. Journal validator requires `operation="workflow_upgrade"`, `phase in {prepared,applied}`, book/from/to revisions, ordered document entries, safe relative paths, exact original/target SHA-256 identity, base64 original bytes, and nullable resulting revision.
- [ ] **Step 4 — GREEN:** `python -m unittest tests.test_workflow_v2_migrations tests.test_workflow_v2_schemas -v`.
- [ ] **Step 5 — Commit:** `feat: add workflow migration registry and journal schema`.

---

### Task 2: Compatibility planner

**Files:**
- Modify: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/source_integrity.py`
- Test: `tests/test_workflow_v2_migration_planner.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class PlannedWrite:
    path: str
    kind: SchemaKind
    original_exists: bool
    original_version: str | None
    original_bytes: bytes | None
    target_data: dict[str, Any]
    target_bytes: bytes
    from_version: int | None
    to_version: int

@dataclass(frozen=True)
class MigrationPlan:
    book_slug: str
    from_revision: str | None
    to_revision: str
    writes: tuple[PlannedWrite, ...]
    lifecycle_downgrades: tuple[int, ...]
    changed: bool

    def write_for(self, path: str) -> PlannedWrite | None: ...

class MigrationPlanner:
    def __init__(self, repository, *, book_dir, artifact_reader, now): ...
    def plan(self, *, slug: str, to_revision: str, installed: Mapping[str, Any]) -> MigrationPlan: ...
```

Source helper:

```python
def build_private_source_manifest_from_identity(
    book_dir: Path,
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> dict[str, Any]: ...
```

- [ ] **Step 1 — RED tests:** target != installed revision fails before writes; future/malformed schemas fail precisely; absent ledger creates empty candidate; reviewed without current PASS downgrades to translated if translation exists; current PASS preserves reviewed; missing/empty translation fails; absent embedded manifest reconstructs from exact source/extracted bytes; private-external reconstructs from metadata identity + extracted hashes without source binary; unprovable source fails; live claim/finalization block; expired claim allowed without lease changes; true current v1/current revision returns `changed=False`.

Key assertion:

```python
plan = planner.plan(slug="legacy", to_revision="new-rev", installed=installed)
metadata_write = plan.write_for("metadata.json")
self.assertIsNotNone(metadata_write)
self.assertEqual(metadata_write.target_data["workflow"]["resolved_revision"], "new-rev")
self.assertEqual(metadata_write.target_data["workflow"]["review_evidence"], "review-ledger-v1")
```

- [ ] **Step 2 — Run RED:** `python -m unittest tests.test_workflow_v2_migration_planner -v`.
- [ ] **Step 3 — Implement raw discovery:** use `repository.storage.read/list`; strict UTF-8/JSON; preserve exact bytes/version. Discover metadata/progress, optional ledger/manifest, sorted claims.
- [ ] **Step 4 — Implement candidate planning:** verify installed target, migrate document shapes, build/reconstruct ledger+manifest, resolve reviewed units with `ReviewLedgerManager`, build metadata target with deterministic `upgrade_history`, validate all target data, serialize each target through `WorkflowStateRepository.serialize()`. Planner never writes.
- [ ] **Step 5 — GREEN:** `python -m unittest tests.test_workflow_v2_migration_planner tests.test_workflow_v2_private_source tests.test_workflow_v2_corpus_manifest -v`.
- [ ] **Step 6 — Commit:** `feat: plan explicit workflow upgrades`.

---

### Task 3: Coordination + transaction executor

**Files:**
- Modify: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/workflow_v2/coordination.py`
- Test: `tests/test_workflow_v2_migration_transaction.py`
- Test: `tests/test_workflow_v2_coordination.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class MigrationResult:
    book_slug: str
    from_revision: str | None
    to_revision: str
    outcome: str  # changed | unchanged | recovered
    migrated_paths: tuple[str, ...]
    lifecycle_downgrades: tuple[int, ...]

class MigrationExecutor:
    def __init__(self, repository, planner: MigrationPlanner, *, coordination): ...
    def execute(self, plan: MigrationPlan, *, session_id: str) -> MigrationResult: ...
    def recover(self, *, session_id: str, installed: Mapping[str, Any]) -> MigrationResult | None: ...
```

`BookCoordinationManager.acquire(operation="workflow_upgrade", ...)` becomes valid.

- [ ] **Step 1 — RED tests:** journal created before target writes; deterministic write order manifest → ledger → sorted claims → progress → metadata; metadata last; captured CAS revisions used; journal CAS-updated after writes; success validates and deletes journal; stale write restores exact original bytes; originally absent paths deleted on rollback; coordination operation accepted/released.
- [ ] **Step 2 — Run RED:** `python -m unittest tests.test_workflow_v2_migration_transaction tests.test_workflow_v2_coordination -v`.
- [ ] **Step 3 — Implement executor:** create canonical journal first; use raw storage writes for target bytes; update journal with resulting versions. On error classify current bytes and rollback exact originals before returning `MigrationConflict`.
- [ ] **Step 4 — Implement recovery:** classify every path by hash as `original|target|unknown`. Unknown → fail closed/no mutation. Mixed known state → rollback, delete journal, re-plan from fresh original state, execute. All target → strict final validation, delete journal, return `recovered` without duplicate history.
- [ ] **Step 5 — GREEN:** same focused test command.
- [ ] **Step 6 — Commit:** `feat: add crash-recoverable workflow upgrade transaction`.

---

### Task 4: Active migration admission + status/resume visibility

**Files:**
- Modify: `scripts/workflow_v2/coordination.py`
- Modify: `scripts/workflow_v2/claims.py`
- Modify: `scripts/workflow_v2/finalize.py`
- Modify: `scripts/workflow_v2/reviews.py`
- Modify: `scripts/workflow_v2/status.py`
- Test: `tests/test_workflow_v2_migration_visibility.py`

**Interfaces:**

```python
def BookCoordinationManager.migration_active(self) -> bool: ...
```

Status adds:

```python
"migration": {"active": False}
# or
"migration": {
  "active": True,
  "phase": "prepared",
  "from_revision": "old",
  "to_revision": "new"
}
```

Resume returns `operation="workflow_upgrade"` before normal lifecycle dispatch when journal is valid/present.

- [ ] **Step 1 — RED tests:** active journal makes `ClaimManager.acquire()` raise `ClaimError`, finalizer raise `FinalizationError`, `ReviewLedgerManager.accept_review()` raise `ReviewEvidenceError`; status exposes migration; resume selects workflow-upgrade; malformed journal invalidates status instead of being ignored.
- [ ] **Step 2 — Run RED:** `python -m unittest tests.test_workflow_v2_migration_visibility -v`.
- [ ] **Step 3 — Minimal guards:** all mutation guards call `BookCoordinationManager.migration_active()`. Status directly reads journal using `SchemaKind.MIGRATION_JOURNAL`; no migration execution logic is duplicated.
- [ ] **Step 4 — GREEN regressions:** `python -m unittest tests.test_workflow_v2_migration_visibility tests.test_workflow_v2_claims tests.test_workflow_v2_finalize tests.test_workflow_v2_reviews tests.test_workflow_v2_status -v`.
- [ ] **Step 5 — Commit:** `feat: gate workflow operations during migration recovery`.

---

### Task 5: CLI `workflow-upgrade`

**Files:**
- Create: `scripts/workflow_v2/migrations_cli.py`
- Modify: `scripts/workflow_v2/review_cli.py` only to extend the existing root registration chain
- Test: `tests/test_workflow_v2_migrations_cli.py`

**Interfaces:**

```python
def load_install_provenance(root: Path) -> dict[str, str | None]: ...
def register_migration_command(subparsers, root: Path, *, error_factory) -> None: ...
```

CLI: `book.py workflow-upgrade <slug> --to <revision> [--json]`.

- [ ] **Step 1 — RED tests:** representative v0 metadata/progress migrates and records old/new revision; merely running status/validate does not rewrite legacy bytes; target mismatch no-write; malformed fixture concise/no traceback/no-write; reviewed without ledger downgrades; private-external works without binary; second identical upgrade byte-idempotent and reports unchanged; JSON deterministic/no wall-clock fields.
- [ ] **Step 2 — Run RED:** `python -m unittest tests.test_workflow_v2_migrations_cli -v`; expected parser missing command.
- [ ] **Step 3 — Implement adapter:** construct `FilesystemStorage` repository, artifact reader, `MigrationPlanner`, `BookCoordinationManager`, `MigrationExecutor`; call `executor.recover(...)` first, otherwise `planner.plan(...)` then `executor.execute(...)`. Adapt `MigrationError`/storage/schema/coordination errors into existing `ReviewCliError` factory. Register lazily at end of `register_review_commands()` after existing status/EPUB registration, avoiding `book.py` changes.
- [ ] **Step 4 — GREEN regressions:** `python -m unittest tests.test_workflow_v2_migrations_cli tests.test_book_cli tests.test_workflow_v2_finalize_cli tests.test_workflow_v2_epub_cli -v`.
- [ ] **Step 5 — Commit:** `feat: add explicit workflow upgrade command`.

---

### Task 6: #18 migration reliability

**Files:**
- Create: `tests/test_workflow_v2_migration_reliability.py`
- Production files only if a new reliability test proves a real defect.

- [ ] **Step 1 — Add durable failure boundaries without production fault hooks:** prepared journal/no target; crash after manifest/ledger but before progress; crash after metadata target before journal delete; stale CAS with exact rollback; unknown concurrent mutation fails closed and preserves unknown bytes/journal; completed rerun changes no durable bytes.
- [ ] **Step 2 — Run:** `python -m unittest tests.test_workflow_v2_migration_reliability -v`. Existing-correct behavior may start GREEN. For a genuine defect preserve RED evidence, make the smallest owning fix, then GREEN.
- [ ] **Step 3 — Commit:** test-only coverage separately from any production defect fix.

---

### Task 7: Full verification and integration audit

- [ ] **Step 1 — Full suite:** `python -m unittest discover -s tests -v`; require exact-final-head success on Python 3.10 and 3.12.
- [ ] **Step 2 — Acceptance audit:** map passing tests to no-silent-upgrade, installed-target check, v0 migration, review downgrade, source reconstruction/private source, admission visibility, metadata-last, rollback, unknown mutation and idempotence.
- [ ] **Step 3 — Diff/ancestry/review audit:** PR base `refactor/workflow-engine-v2`; `behind_by=0`; merge-base equals integration SHA at branch creation; only #16 docs/migration/admission/tests changed; no unresolved review threads/comments/request-changes; `main` unchanged.
- [ ] **Step 4 — Ready/merge:** update PR with exact RED/GREEN/final CI evidence; Ready only after clean guards; guarded merge with expected-head SHA only to integration; preserve feature branch; never merge to `main`.
