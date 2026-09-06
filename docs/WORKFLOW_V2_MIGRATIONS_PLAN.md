# Workflow v2 Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit, crash-recoverable Workflow v2 upgrades from provable legacy v0/v1 state to the currently installed workflow revision without silent provenance or review/source fabrication.

**Architecture:** `MigrationPlanner` builds a validated immutable plan; `MigrationExecutor` owns coordination, journaled CAS application, rollback and recovery. `migration_journal.py` strictly validates transient recovery state without extending the central book-document `SchemaKind` registry. CLI and existing workflow operations only adapt around these APIs.

**Tech Stack:** Python 3.10+, stdlib (`json`, `base64`, `hashlib`, `datetime`, `copy`), existing storage/repository/review/source/coordination primitives, `unittest`.

**Spec:** `docs/WORKFLOW_V2_MIGRATIONS_DESIGN.md`

## Global Constraints

- No silent durable migration outside explicit `workflow-upgrade`.
- Missing `schema_version` is logical v0 only in migration code.
- `--to` equals installed `resolved_revision` exactly.
- Never invent claim/review/source provenance.
- Metadata writes last.
- `.workflow/migration.json` is authoritative while present.
- Unknown concurrent bytes are never overwritten.
- No third-party runtime dependency.
- Merge only to `refactor/workflow-engine-v2`; `main` unchanged.

---

### Task 1: Pure registry + journal validator

**Files:**
- Create: `scripts/workflow_v2/migrations.py`
- Create: `scripts/workflow_v2/migration_journal.py`
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

Journal API:

```python
MIGRATION_PATH = ".workflow/migration.json"
class MigrationJournalError(RuntimeError): ...
def validate_migration_journal(data: Mapping[str, Any]) -> dict[str, Any]: ...
def serialize_migration_journal(data: Mapping[str, Any]) -> bytes: ...
def load_migration_journal(storage: StorageBackend) -> tuple[dict[str, Any], str]: ...
```

- [ ] **Step 1 — RED:** v0 supported documents add only schema_version and validate as v1; v1 unchanged; version 2/incomplete v0 fail precisely. Journal validator accepts valid prepared/applied forms and rejects phase/operation/path/hash/base64/original-null inconsistencies.
- [ ] **Step 2 — Run:** `python -m unittest tests.test_workflow_v2_migrations -v`; expected assertion failures for missing migration/journal APIs only.
- [ ] **Step 3 — Implement:** registry deep-copies and delegates to `parse_document`; journal validator is domain-local, strict, canonical JSON serializer, no review/finalize/status imports.
- [ ] **Step 4 — GREEN:** `python -m unittest tests.test_workflow_v2_migrations tests.test_workflow_v2_schemas -v`.
- [ ] **Step 5 — Commit:** `feat: add workflow migration registry and journal validation`.

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
def build_private_source_manifest_from_identity(book_dir, metadata, progress) -> dict[str, Any]: ...
```

- [ ] **Step 1 — RED:** target mismatch, future/malformed schema, missing ledger, review downgrade/current PASS, missing translation, embedded/private manifest reconstruction, unprovable source, live/expired claim, finalization block, true no-op.
- [ ] **Step 2 — Run:** `python -m unittest tests.test_workflow_v2_migration_planner -v`.
- [ ] **Step 3 — Implement raw discovery:** strict UTF-8/JSON through storage, preserve exact bytes/revisions, discover sorted claims.
- [ ] **Step 4 — Implement candidate planning:** installed-target check; schema migrations; candidate ledger/manifest; reviewed lifecycle reconciliation; deterministic metadata workflow/history; strict target validation; canonical target bytes via repository serializer. No writes.
- [ ] **Step 5 — GREEN:** planner + private-source/corpus focused suites.
- [ ] **Step 6 — Commit:** `feat: plan explicit workflow upgrades`.

---

### Task 3: Coordination + transaction executor

**Files:**
- Modify: `scripts/workflow_v2/migrations.py`
- Modify: `scripts/workflow_v2/coordination.py`
- Modify: `scripts/workflow_v2/schemas.py` only to allow coordination operation `workflow_upgrade`
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

- [ ] **Step 1 — RED:** journal before target writes; deterministic order manifest→ledger→claims→progress→metadata; metadata last; captured CAS; journal revision updates; success deletes journal; stale write exact rollback; originally absent target deleted; coordination operation accepted.
- [ ] **Step 2 — Run focused RED.**
- [ ] **Step 3 — Implement:** raw target writes from canonical plan bytes; journal stores exact original base64/hash; on failure classify and rollback exact originals.
- [ ] **Step 4 — Recovery:** every path `original|target|unknown`; unknown fail closed/no mutation; known mixture rollback/delete/replan/execute; all target strict validate/delete journal/`recovered`.
- [ ] **Step 5 — GREEN focused transaction/coordination tests.**
- [ ] **Step 6 — Commit:** `feat: add crash-recoverable workflow upgrade transaction`.

---

### Task 4: Active migration visibility/admission

**Files:**
- Modify: `scripts/workflow_v2/coordination.py`
- Modify: `scripts/workflow_v2/claims.py`
- Modify: `scripts/workflow_v2/finalize.py`
- Modify: `scripts/workflow_v2/reviews.py`
- Modify: `scripts/workflow_v2/status.py`
- Test: `tests/test_workflow_v2_migration_visibility.py`

**Interfaces:** `BookCoordinationManager.migration_active() -> bool` uses `load_migration_journal(storage)`. Status adds bounded `migration` section; resume prioritizes `operation="workflow_upgrade"`.

- [ ] **Step 1 — RED:** active journal blocks `ClaimManager.acquire` (`ClaimError`), finalizer (`FinalizationError`), `accept_review` (`ReviewEvidenceError`); status exposes migration; resume selects workflow-upgrade; malformed journal invalidates status.
- [ ] **Step 2 — Run RED.**
- [ ] **Step 3 — Implement minimal guards/routing; no migration execution logic in these modules.**
- [ ] **Step 4 — GREEN visibility + existing claim/finalize/review/status regressions.**
- [ ] **Step 5 — Commit:** `feat: gate workflow operations during migration recovery`.

---

### Task 5: CLI `workflow-upgrade`

**Files:**
- Create: `scripts/workflow_v2/migrations_cli.py`
- Modify: `scripts/workflow_v2/review_cli.py` only to extend root registration chain
- Test: `tests/test_workflow_v2_migrations_cli.py`

**Interfaces:**

```python
def load_install_provenance(root: Path) -> dict[str, str | None]: ...
def register_migration_command(subparsers, root: Path, *, error_factory) -> None: ...
```

- [ ] **Step 1 — RED:** representative v0 upgrade records revisions; ordinary status/validate does not rewrite; target mismatch no-write; malformed fixture concise no-write; reviewed downgrade; private source no binary; second upgrade byte-idempotent unchanged; deterministic JSON.
- [ ] **Step 2 — Run:** parser should fail because command absent.
- [ ] **Step 3 — Implement:** build filesystem repository/artifact reader/planner/coordination/executor; `recover()` first, otherwise `plan()` then `execute()`; adapt errors into existing `ReviewCliError` factory; lazy register at end of `register_review_commands()` without modifying `book.py`.
- [ ] **Step 4 — GREEN CLI + book/finalize/EPUB regressions.**
- [ ] **Step 5 — Commit:** `feat: add explicit workflow upgrade command`.

---

### Task 6: #18 migration reliability

**Files:**
- Create: `tests/test_workflow_v2_migration_reliability.py`
- Production only if a reliability test demonstrates a real defect.

- [ ] **Step 1:** durable boundaries: prepared journal/no target; crash after manifest/ledger; crash after metadata before journal deletion; CAS conflict rollback; unknown mutation preserves unknown bytes+journal; completed rerun byte-idempotent.
- [ ] **Step 2:** `python -m unittest tests.test_workflow_v2_migration_reliability -v`; preserve RED evidence for genuine defects before minimal fix.
- [ ] **Step 3:** commit test-only coverage separately from any production fix.

---

### Task 7: Full verification/audit

- [ ] **Step 1:** `python -m unittest discover -s tests -v`; exact final head GREEN Python 3.10 + 3.12.
- [ ] **Step 2:** acceptance audit maps tests to no-silent-upgrade, target proof, v0 migration, review downgrade, source reconstruction, admission visibility, metadata-last, rollback, unknown mutation, idempotence.
- [ ] **Step 3:** PR base integration; `behind_by=0`; merge-base equals branch-creation integration SHA; only #16 docs/migration/admission/tests; no unresolved comments/reviews; `main` unchanged.
- [ ] **Step 4:** update PR evidence; Ready only after clean guards; expected-head guarded merge only into integration; preserve feature branch; never merge `main`.
