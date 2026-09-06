# Workflow v2 Atomic Finalize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an idempotent, crash-recoverable `book.py finalize <slug>` that serializes finalization against claim admission, promotes all chapters with one progress CAS, and generates deterministic completion reports.

**Architecture:** A short-lived coordination mutex serializes claim admission versus installation of a transient finalization marker. Finalization stores backend-neutral candidate content identity, performs one all-reviewed `progress.json` CAS, recovers across crash windows, and projects authoritative state into `REVIEW_REPORT.md`, `STATE.md`, and `FINAL_QUALITY_GATES.md`.

**Tech Stack:** Python 3.10+, stdlib only, existing `WorkflowStateRepository`, `StorageBackend`, `ClaimManager`, `ReviewLedgerManager`, `StatusResolver`, `review_report` module, unittest/GitHub Actions matrix.

**Spec:** `docs/WORKFLOW_V2_FINALIZE_DESIGN.md`

## Global Constraints

- Target only `refactor/workflow-engine-v2`; never change `main`.
- Preserve feature branch after merge.
- No schema-version bump for existing metadata/progress; #16 owns migrations.
- No EPUB build (#14), GitHub API backend (#17), or parallel reconciliation (#15).
- Generated Markdown is projection-only and contains no new wall-clock generation timestamp.
- Every production slice follows RED → minimal GREEN → full Python 3.10/3.12 matrix before the next behavioral slice.
- Final PR must be `behind_by=0`, mergeable, with no unresolved comments/reviews/threads.

---

## File map

- `scripts/workflow_v2/schemas.py` — strict transient coordination/finalization lock schemas.
- `scripts/workflow_v2/repository.py` — public canonical document serialization helper used by writes and candidate hashing.
- `scripts/workflow_v2/coordination.py` — short-lived book admission mutex.
- `scripts/workflow_v2/claims.py` — claim admission through the coordination mutex; reject active finalization.
- `scripts/workflow_v2/finalize.py` — backend-neutral preflight, marker recovery, one-CAS promotion, completion snapshot and Markdown renderers.
- `scripts/workflow_v2/status.py` — expose active finalization and route resume to finalize recovery.
- `scripts/workflow_v2/reviews.py` — reject direct `accept_review` while finalization is active.
- `scripts/workflow_v2/status_cli.py` — expose shared filesystem structural/corpus preflight for finalize CLI reuse.
- `scripts/workflow_v2/finalize_cli.py` — filesystem adapter, canonical report writes, `finalize` command.
- `scripts/book.py` — register finalize command and top-level expected error.
- `tests/test_workflow_v2_coordination.py` — mutex/claim admission races and expiry.
- `tests/test_workflow_v2_finalize.py` — preflight, atomic CAS, recovery and snapshot/rendering.
- `tests/test_workflow_v2_finalize_cli.py` — end-to-end command/report/idempotence/error behavior.
- `tests/test_workflow_v2_status.py`, `tests/test_workflow_v2_reviews.py` — recovery visibility and direct-promotion admission.
- `tests/test_workflow_v2_reliability.py` — #18 interrupted-finalize/idempotence scenarios.

---

### Task 1: Canonical serialization and transient lock schemas

**Files:**
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/workflow_v2/repository.py`
- Modify: `tests/test_workflow_v2_schemas.py`
- Modify: `tests/test_workflow_v2_repository.py`

**Interfaces:**
- Produces: `SchemaKind.COORDINATION_LOCK`, `SchemaKind.FINALIZATION_LOCK`.
- Produces: `WorkflowStateRepository.serialize(path: str, schema: SchemaKind, data: Mapping[str, object]) -> bytes`.
- Existing `create()` and `write_if_version()` must call the same public serializer.

- [ ] **Step 1: Add RED schema tests**

Add tests that accept exactly:

```python
coordination = {
    "schema_version": 1,
    "lock_id": "a" * 32,
    "operation": "claim_admission",
    "session_id": "session-a",
    "acquired_at": "2026-09-06T20:00:00Z",
    "expires_at": "2026-09-06T20:01:00Z",
}
finalization = {
    "schema_version": 1,
    "lock_id": "b" * 32,
    "book_slug": "demo",
    "workflow_revision": "workflow-rev",
    "base_progress_revision": "base-rev",
    "candidate_progress_sha256": "c" * 64,
    "phase": "preparing",
    "promoted_progress_revision": None,
    "session_id": "session-a",
    "started_at": "2026-09-06T20:00:00Z",
}
```

Reject invalid operation, invalid timestamp interval, invalid candidate SHA, `phase=preparing` with non-null promoted revision, and `phase=promoted` with null promoted revision.

- [ ] **Step 2: Add RED repository serialization test**

Assert `repository.serialize("progress.json", SchemaKind.PROGRESS, data)` returns the exact bytes later persisted by `create()` and that invalid schema data fails before any storage mutation.

- [ ] **Step 3: Run full tests for RED witness**

Run: `python -m unittest discover -s tests -v`
Expected: only the new lock-schema/serializer tests fail because the enum members/public serializer do not exist.

- [ ] **Step 4: Implement minimal schema validators and public serializer**

Use existing `_validate_hex_id`, `_parse_utc_timestamp`, `_validate_sha256`, and strict phase relationship validation. Rename/replace repository `_serialize` with public `serialize`; route create/write through it.

- [ ] **Step 5: Run full matrix and commit**

Expected: all tests GREEN on Python 3.10 and 3.12.
Commit: `feat: add finalize coordination schemas`

---

### Task 2: Serialize claim admission against finalization

**Files:**
- Create: `scripts/workflow_v2/coordination.py`
- Modify: `scripts/workflow_v2/claims.py`
- Create: `tests/test_workflow_v2_coordination.py`
- Modify: existing claim tests only where constructor injection is required.

**Interfaces:**

```python
@dataclass(frozen=True)
class CoordinationLease:
    path: str
    data: dict[str, Any]
    version: str

class CoordinationError(RuntimeError): ...
class CoordinationConflict(CoordinationError): ...

class BookCoordinationManager:
    def __init__(self, repository, *, now=None, id_factory=None): ...
    def acquire(self, *, operation: str, session_id: str, lease_seconds: int = 60) -> CoordinationLease: ...
    def release(self, lease: CoordinationLease) -> None: ...
    def finalization_active(self) -> bool: ...
```

`ClaimManager.__init__` adds optional `coordination: BookCoordinationManager | None = None`; default coordinator shares the claim clock but uses its own UUID factory so existing deterministic claim-ID tests are not renumbered.

- [ ] **Step 1: Write RED coordination tests**

Cover live mutex conflict, exact-expiry cleanup, version-safe stale deletion, and `finalization_active()` schema validation.

- [ ] **Step 2: Write RED claim admission tests**

Cover:

```python
# finalization marker already exists -> acquire raises ClaimConflict/coordination-derived claim error
# claim admission holds mutex while creating unit claims
# finalize admission cannot acquire same mutex concurrently
# after mutex release ordinary disjoint claims retain existing behavior
```

Use an instrumented storage in one race test to pause immediately after mutex acquisition and prove the competing admission cannot pass.

- [ ] **Step 3: Run RED**

Expected: failures only because `coordination.py` and claim integration do not exist.

- [ ] **Step 4: Implement coordinator and minimal ClaimManager integration**

Acquire mutex before existing range conflict/create logic, check `.workflow/finalization.json` while mutex is held, release in `finally`. Do not alter release/cleanup claim semantics.

- [ ] **Step 5: Run full matrix and commit**

Commit: `feat: serialize claim admission with finalization`

---

### Task 3: Finalization preflight and one-CAS promotion

**Files:**
- Create: `scripts/workflow_v2/finalize.py`
- Create: `tests/test_workflow_v2_finalize.py`

**Interfaces:**

```python
FINALIZATION_PATH = ".workflow/finalization.json"

class FinalizationError(RuntimeError): ...
class FinalizationBlocked(FinalizationError): ...
class FinalizationConflict(FinalizationError): ...

@dataclass(frozen=True)
class FinalizeResult:
    snapshot: dict[str, Any]
    progress_revision: str
    promoted: bool
    recovered: bool

PreflightProvider = Callable[[], tuple[Sequence[str], Mapping[str, Any]]]

class FinalizationManager:
    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        artifact_reader: Callable[[str], bytes],
        preflight: PreflightProvider,
        coordination: BookCoordinationManager | None = None,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ): ...

    def finalize(self, *, session_id: str) -> FinalizeResult: ...
```

Internal pure helpers:

```python
def build_reviewed_candidate(progress: Mapping[str, Any]) -> dict[str, Any]: ...
def sha256_bytes(content: bytes) -> str: ...
def build_completion_snapshot(...) -> dict[str, Any]: ...
```

- [ ] **Step 1: Write RED precondition tests**

Each scenario records `progress.json`, report files and marker paths before/after and asserts zero mutation on clean failure:
- active claim;
- corpus `unsealed` or `invalid`;
- structural errors;
- untranslated/empty translation;
- review `missing`, `stale`, or `corrections_required`.

- [ ] **Step 2: Run RED and implement read-only preflight**

Use current metadata/progress + `ReviewLedgerManager.resolve_all()` + injected structural/corpus preflight. Do not mutate progress in this step.

- [ ] **Step 3: Write RED atomic-promotion tests**

Instrument repository/storage writes and assert a successful two-or-more-chapter finalize performs exactly one conditional write to `progress.json`, with every chapter `reviewed`. Inject a stale progress revision between admission and CAS; assert no partial promotion.

- [ ] **Step 4: Implement marker admission and one progress CAS**

While `finalize_admission` mutex is held: create/adopt marker and verify zero claims. Revalidate immediately before progress CAS. Serialize candidate with `repository.serialize`, hash bytes, and write whole progress once.

- [ ] **Step 5: Write RED crash-recovery tests**

Cover persisted states representing:
- `preparing` marker + base progress;
- `preparing` marker + candidate progress bytes (crash after CAS before marker update);
- `promoted` marker + exact promoted revision/content;
- incompatible marker/current progress combination.

- [ ] **Step 6: Implement phase recovery**

After progress CAS, update marker to `promoted` with actual returned backend revision. For the crash window, compare current raw progress SHA-256 with marker candidate hash before marker phase update.

- [ ] **Step 7: Run full matrix and commit**

Commit: `feat: add atomic recoverable finalize core`

---

### Task 4: Recovery visibility and competing lifecycle mutation guard

**Files:**
- Modify: `scripts/workflow_v2/status.py`
- Modify: `scripts/workflow_v2/reviews.py`
- Modify: `tests/test_workflow_v2_status.py`
- Modify: `tests/test_workflow_v2_reviews.py`

**Interfaces:**

Status adds:

```json
{"finalization": {"active": false}}
```

or, when active:

```json
{
  "finalization": {
    "active": true,
    "phase": "preparing",
    "workflow_revision": "...",
    "started_at": "..."
  }
}
```

Resume returns `operation="finalize"` before ordinary unit selection when status is valid and finalization is active.

- [ ] **Step 1: RED status/resume tests**

Assert valid marker is visible and selected as next operation; malformed marker or book/workflow mismatch invalidates status.

- [ ] **Step 2: RED direct-promotion test**

Create a valid current PASS + finalization marker and assert `ReviewLedgerManager.accept_review()` refuses to mutate progress.

- [ ] **Step 3: Implement minimal reads/guards**

Read marker through `SchemaKind.FINALIZATION_LOCK`; expose bounded fields only. In `accept_review`, check marker before lifecycle CAS and raise `ReviewConflict`/`ReviewEvidenceError` with a stable message.

- [ ] **Step 4: Full matrix and commit**

Commit: `feat: expose finalize recovery in workflow status`

---

### Task 5: Completion projections and CLI

**Files:**
- Modify: `scripts/workflow_v2/finalize.py`
- Modify: `scripts/workflow_v2/status_cli.py`
- Create: `scripts/workflow_v2/finalize_cli.py`
- Modify: `scripts/book.py`
- Create: `tests/test_workflow_v2_finalize_cli.py`

**Interfaces:**

Expose shared filesystem preflight from status CLI:

```python
def default_preflight(root: Path, slug: str) -> tuple[Sequence[str], Mapping[str, Any]]: ...
```

Keep existing status/resume behavior by routing both through this function.

Finalize rendering:

```python
def render_state_markdown(snapshot: Mapping[str, Any]) -> str: ...
def render_quality_gates_markdown(snapshot: Mapping[str, Any]) -> str: ...
```

CLI adapter:

```python
class FinalizeCliError(RuntimeError): ...
def register_finalize_command(subparsers, root: Path) -> None: ...
```

- [ ] **Step 1: RED deterministic rendering tests**

Assert repeated rendering produces identical bytes, includes book/workflow/revisions/lifecycle/review coverage/corpus mode, and contains no `generated_at`.

- [ ] **Step 2: Implement completion snapshot/renderers**

Build the snapshot only from post-promotion authoritative state and #21 review snapshot. `REVIEW_REPORT.md` uses the existing renderer unchanged.

- [ ] **Step 3: RED CLI tests**

End-to-end fixture must verify:
- `book.py finalize demo` promotes all chapters and creates all three reports;
- second run is idempotent and report bytes are identical;
- `--json` returns deterministic success payload after performing/confirming finalize;
- malformed ledger/corpus failure exits 1 without traceback or partial progress promotion;
- existing active claim blocks and remains intact;
- `private_external` verified corpus succeeds without source binary.

- [ ] **Step 4: Implement filesystem adapter and report CAS writes**

All report content is rendered before its write. Use `FilesystemStorage` create/update CAS; identical bytes are `unchanged`. After fresh postflight and byte verification, delete finalization marker with version guard.

- [ ] **Step 5: Wire `book.py`**

Register finalize command and add `FinalizeCliError` to the top-level expected exception tuple.

- [ ] **Step 6: Full matrix and commit**

Commit: `feat: add finalize command and completion reports`

---

### Task 6: #18 interrupted-finalize reliability extension

**Files:**
- Modify: `tests/test_workflow_v2_reliability.py`

- [ ] **Step 1: Add fresh-process failure-injection tests**

Cover:
- crash after finalization marker before progress CAS, then fresh `resume` selects finalize and retry completes;
- crash after progress CAS before marker phase update, then retry does not rewrite progress;
- crash after only one generated report, then retry regenerates the canonical set and removes marker;
- successful finalize rerun has identical progress/report bytes and no marker.

Use actual temporary repository state and subprocess CLI where practical; do not add production fault-injection hooks solely for tests.

- [ ] **Step 2: RED/GREEN only if a real defect is found**

If tests expose a production defect, preserve the failing witness commit/run, diagnose root cause, apply the smallest owning-component fix, and run a fresh full matrix.

- [ ] **Step 3: Commit**

Commit: `test: cover interrupted workflow finalization`

---

### Task 7: Completion audit and integration

- [ ] Run final Python 3.10/3.12 matrix on the exact branch head and record test count/log evidence.
- [ ] Review full PR diff against `docs/WORKFLOW_V2_FINALIZE_DESIGN.md`; specifically recheck marker cleanup, report authority boundary, and no sequential lifecycle promotion.
- [ ] Confirm PR comments, submitted reviews and inline threads are empty/resolved.
- [ ] Confirm `feature/workflow-v2-finalize` is `behind_by=0` relative to `refactor/workflow-engine-v2`.
- [ ] Confirm `main` is unchanged.
- [ ] Update PR body with all RED/GREEN run IDs, changed files, recovery semantics and audit evidence.
- [ ] Mark Ready only after final GREEN/audit.
- [ ] Merge with expected-head guard only into `refactor/workflow-engine-v2`.
- [ ] Verify merge commit, issue #12 state, preserved feature branch and unchanged `main`.
