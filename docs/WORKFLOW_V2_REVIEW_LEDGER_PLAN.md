# Workflow v2 Review Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #9 so Reviewer outcomes become immutable, hash-bound machine evidence, stale reviews are detected from current artifacts, and `progress.json` can reach `reviewed` only through a current PASS gate.

**Architecture:** Keep one book-local `review-ledger.json` as a CAS-protected append-only logical history. Put review semantics in a backend-neutral `ReviewLedgerManager` that receives canonical progress/metadata documents plus an injected artifact reader, reuses #8 claim ownership and storage CAS primitives, computes exact SHA-256 identities itself, resolves current/stale/missing evidence deterministically, and exposes a guarded progress promotion operation. `book.py` remains an adapter for initialization, filesystem artifact reads, CLI parsing, validation, and best-effort Git commit provenance.

**Tech Stack:** Python 3.10+, standard library only (`hashlib`, `datetime`, `uuid`, `json`, `argparse`, `unittest`), existing Workflow v2 schema/repository/storage/claim modules.

**Spec:** `docs/WORKFLOW_V2_REVIEW_LEDGER_DESIGN.md`

## Global Constraints

- Repository state is authoritative over chat history.
- `progress.json` is lifecycle state only and never proves review coverage.
- New #9-capable books set `metadata.workflow.review_evidence = "review-ledger-v1"` and create `review-ledger.json` with `next_sequence = 1` and no records.
- Existing books without the marker retain prior workflow semantics and are not silently migrated.
- Ledger-enabled review recording requires immutable `metadata.workflow.resolved_revision`; requested refs are not accepted as immutable review provenance.
- Review records are append-only logical evidence; existing records are never rewritten individually.
- Source and translation hashes are SHA-256 over exact canonical file bytes; no normalization is permitted.
- Recording requires a live matching reviewer claim for the same canonical unit and session. Expired claims cannot record evidence and remain occupied until #8 cleanup.
- A ledger CAS conflict is surfaced and never blindly retried with the old Reviewer result.
- `status=reviewed` is valid for ledger-enabled books only when current exact PASS evidence exists.
- No generated report (#21), status/resume (#10), finalize (#12), migration (#16), GitHub backend (#17), or parallel scheduling (#15) is implemented here.
- Python 3.10 and 3.12 complete suites must pass before the stacked PR is review-ready.

---

### Task 1: Strict review-ledger schema and new-book initialization

**Files:**
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/book.py`
- Modify: `tests/test_workflow_v2_schemas.py`
- Modify: `tests/test_book_cli.py`

**Interfaces:**
- Consumes: `SchemaKind.REVIEW_LEDGER`, `SCHEMA_VERSION`, existing metadata/progress repository writes.
- Produces: strict `REVIEW_LEDGER` validation and deterministic ledger initialization for new books.

- [ ] **Step 1: Write failing schema tests**

Require a complete ledger:

```python
ledger = {
    "schema_version": 1,
    "book_slug": "sample",
    "next_sequence": 2,
    "records": [{
        "record_id": "00000000000000000000000000000001",
        "sequence": 1,
        "unit_id": "chapter-000001",
        "outcome": "PASS",
        "source_sha256": "a" * 64,
        "translation_sha256": "b" * 64,
        "workflow_revision": "0123456789abcdef",
        "review_contract_revision": "docs/TRANSLATION.md@0123456789abcdef",
        "reviewer_session_id": "reviewer-a",
        "reviewed_at": "2026-09-06T00:00:00Z",
        "state_revision": "progress-revision",
        "review_commit": None,
        "correction_round": 0,
        "supersedes_record_id": None,
    }],
}
self.assertEqual(parse_document(SchemaKind.REVIEW_LEDGER, ledger).data, ledger)
```

Add invalid cases for duplicate IDs, duplicate/non-increasing sequences, inconsistent `next_sequence`, invalid hashes/unit/outcome/timestamp, negative correction round, broken supersession target, cross-unit supersession, forked chains, and a first record that incorrectly supersedes another record.

- [ ] **Step 2: Write failing extraction tests**

Extend `test_extract_markdown_creates_complete_book_state` / provenance coverage to require:

```python
metadata = json.loads((book / "metadata.json").read_text())
self.assertEqual(metadata["workflow"]["review_evidence"], "review-ledger-v1")
ledger = json.loads((book / "review-ledger.json").read_text())
self.assertEqual(ledger, {
    "schema_version": 1,
    "book_slug": "sample",
    "next_sequence": 1,
    "records": [],
})
```

Extraction must still succeed when install provenance is absent; the marker/empty ledger are created, while later review recording will fail until immutable workflow provenance exists.

- [ ] **Step 3: Run focused tests and verify RED**

Run through CI:

```text
python -m unittest tests.test_workflow_v2_schemas tests.test_book_cli -v
```

Expected: failures because the current ledger validator only checks `book_slug`/`records`, and extraction does not create the marker/ledger.

- [ ] **Step 4: Implement strict ledger validation**

In `_validate_review_ledger`, validate every record and then domain-shape invariants in stored order. Use existing `_validate_sha256`, `_validate_unit_id`, `_parse_utc_timestamp`, `_require_*` helpers. Track:

```python
record_ids: set[str] = set()
sequences: set[int] = set()
last_by_unit: dict[str, str] = {}
superseded_by: dict[str, str] = {}
```

For each record require `supersedes_record_id == last_by_unit.get(unit_id)`. Reject any target already present in `superseded_by`, then update `last_by_unit[unit_id] = record_id`. Require `next_sequence == 1` for empty history and `next_sequence == records[-1]["sequence"] + 1` otherwise.

- [ ] **Step 5: Initialize ledger-enabled books**

In `extract_command`, extend the workflow dictionary before metadata serialization:

```python
workflow = workflow_provenance()
workflow["review_evidence"] = "review-ledger-v1"
```

Create metadata, progress, and ledger through `WorkflowStateRepository`; ledger creation occurs before support files are reported as complete:

```python
repository.create("review-ledger.json", SchemaKind.REVIEW_LEDGER, {
    "schema_version": SCHEMA_VERSION,
    "book_slug": slug,
    "next_sequence": 1,
    "records": [],
})
```

- [ ] **Step 6: Run focused tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_schemas tests.test_book_cli -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```text
workflow: define and initialize review ledger
```

---

### Task 2: Review recording and deterministic current-evidence resolution

**Files:**
- Create: `scripts/workflow_v2/reviews.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Create: `tests/test_workflow_v2_reviews.py`

**Interfaces:**
- Consumes: `WorkflowStateRepository`, `SchemaKind.REVIEW_LEDGER`, #8 claim files, `canonical_unit_id`, exact progress revision, injected `artifact_reader(path: str) -> bytes`, injected UTC clock and ID factory.
- Produces:
  - `ReviewLedgerManager.record(progress, progress_revision, metadata, chapter_number, *, outcome, reviewer_session_id, review_commit=None) -> ReviewRecordResult`
  - `ReviewLedgerManager.resolve_unit(progress, metadata, chapter_number) -> ReviewResolution`
  - `ReviewLedgerManager.resolve_all(progress, metadata) -> list[ReviewResolution]`
  - errors `ReviewError`, `ReviewConflict`, `ReviewClaimError`, `ReviewEvidenceError`.

- [ ] **Step 1: Write failing resolver tests**

Use a temp repository, deterministic artifact reader, fixed clock/IDs, one reviewer claim, and initialized ledger. Require:

```python
result = manager.record(... outcome="PASS", reviewer_session_id="reviewer-a")
resolution = manager.resolve_unit(progress, metadata, 1)
self.assertEqual(resolution.state, "pass")
self.assertEqual(resolution.source_sha256, sha256(source_bytes).hexdigest())
self.assertEqual(resolution.translation_sha256, sha256(translation_bytes).hexdigest())
```

Then mutate only translation bytes and require `state == "stale"`; restore exact prior bytes and require the exact prior PASS to become current again. Repeat with changed source bytes and changed expected review-contract/workflow identity.

Also require `missing` when there is no unit history and `untranslated` when the canonical translation file is missing or empty.

- [ ] **Step 2: Write failing record/claim tests**

Cover:

- caller-provided hashes do not exist in the API;
- missing `metadata.workflow.resolved_revision` raises `ReviewEvidenceError`;
- missing reviewer claim raises `ReviewClaimError`;
- translator claim raises `ReviewClaimError`;
- foreign reviewer session raises `ReviewClaimError`;
- expired reviewer claim raises `ReviewClaimError` without deleting the claim;
- claim workflow revision mismatch raises `ReviewClaimError`;
- missing/empty translation refuses record creation;
- first PASS uses correction round `0`;
- first `CORRECTIONS_REQUIRED` uses round `1`;
- PASS after correction retains round `1`;
- later `CORRECTIONS_REQUIRED` increments to round `2`;
- `supersedes_record_id` always links the immediately preceding unit record.

- [ ] **Step 3: Run focused tests and verify RED**

```text
python -m unittest tests.test_workflow_v2_reviews -v
```

Expected: import/API failures because `reviews.py` does not exist.

- [ ] **Step 4: Implement focused review-domain types**

Define immutable dataclasses:

```python
@dataclass(frozen=True)
class ReviewRecordResult:
    record: dict[str, Any]
    ledger_revision: str

@dataclass(frozen=True)
class ReviewResolution:
    unit_id: str
    chapter_number: int
    state: str  # pass|corrections_required|stale|missing|untranslated
    source_sha256: str | None
    translation_sha256: str | None
    current_record: dict[str, Any] | None
    history: tuple[dict[str, Any], ...]
```

Constructor:

```python
ReviewLedgerManager(
    repository,
    *,
    artifact_reader,
    now=None,
    id_factory=None,
)
```

The manager validates chapter identity from progress and reads canonical artifact paths only through `artifact_reader`.

- [ ] **Step 5: Implement immutable provenance and claim validation**

`_workflow_revision(metadata)` accepts only non-empty `metadata["workflow"]["resolved_revision"]`. Expected review contract is `docs/TRANSLATION.md@<resolved_revision>` for this v3 contract.

`_require_reviewer_claim(unit_id, session_id, workflow_revision)` reads `.workflow/claims/<unit>.json`, requires role reviewer, matching session/workflow, and requires `expires_at > now`. Expired-but-present ownership remains a claim conflict for others under #8 but cannot authorize a new review record.

- [ ] **Step 6: Implement artifact identity and resolution**

Hash exact bytes:

```python
def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
```

Resolve all unit history sorted by sequence. Exact matches require unit/source hash/translation hash/workflow/review contract identity. Highest-sequence exact match determines `pass` vs `corrections_required`; history with no exact match is `stale`. Missing/empty translation is `untranslated`.

- [ ] **Step 7: Implement CAS record append**

Read and validate the ledger with its exact revision; derive the record ID, sequence, supersession, and correction round; append one record; call:

```python
new_revision = repository.write_if_version(
    "review-ledger.json",
    SchemaKind.REVIEW_LEDGER,
    new_ledger,
    loaded.version,
)
```

Convert `StorageVersionConflict` to `ReviewConflict`. Never retry internally.

- [ ] **Step 8: Add deterministic concurrent-writer test**

Use a barrier around the first ledger read in two threads/processes so both writers start from one ledger revision. Require exactly one successful append and one `ReviewConflict`, with one durable new record.

- [ ] **Step 9: Run focused tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_reviews tests.test_workflow_v2_claims tests.test_workflow_v2_storage -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```text
workflow: add hash-bound review evidence
```

---

### Task 3: Guarded reviewed promotion and ledger-aware structural validation

**Files:**
- Modify: `scripts/workflow_v2/reviews.py`
- Modify: `scripts/book.py`
- Modify: `tests/test_workflow_v2_reviews.py`
- Modify: `tests/test_book_cli.py`

**Interfaces:**
- Consumes: `ReviewLedgerManager.resolve_unit`, exact progress revision, `WorkflowStateRepository.write_if_version`.
- Produces: `ReviewLedgerManager.accept_review(...) -> AcceptReviewResult` and ledger-aware `book.py validate` behavior.

- [ ] **Step 1: Write failing promotion tests**

Require:

```python
accepted = manager.accept_review(progress, progress_revision, metadata, 1)
self.assertEqual(accepted.unit_id, "chapter-000001")
self.assertEqual(accepted.status, "reviewed")
```

Cases:

- no PASS -> `ReviewEvidenceError`;
- current `CORRECTIONS_REQUIRED` -> error;
- stale translation/source -> error;
- chapter not `translated`/`reviewed` -> error;
- current PASS promotes only selected chapter;
- stale progress revision -> `ReviewConflict`, no mutation;
- already reviewed + current PASS -> idempotent and returns existing progress revision without rewriting;
- already reviewed + stale evidence -> error.

- [ ] **Step 2: Write failing validation tests**

For a ledger-enabled book:

- missing `review-ledger.json` is invalid;
- malformed ledger is invalid;
- `status=reviewed` with no current PASS is invalid;
- exact PASS + reviewed status validates;
- editing the reviewed translation makes `validate` fail as stale;
- a book without `workflow.review_evidence` retains legacy validation behavior and is not required to contain a ledger.

- [ ] **Step 3: Run focused tests and verify RED**

```text
python -m unittest tests.test_workflow_v2_reviews tests.test_book_cli -v
```

Expected: missing `accept_review` and ledger-aware validation behavior.

- [ ] **Step 4: Implement `accept_review`**

Define:

```python
@dataclass(frozen=True)
class AcceptReviewResult:
    unit_id: str
    status: str
    progress_revision: str
    changed: bool
```

Require current exact `pass`, then deep-copy progress, set only selected chapter to `reviewed`, and write via exact CAS. Already-reviewed/current-PASS returns `changed=False` and does not rewrite.

- [ ] **Step 5: Add a reusable ledger-validation adapter in `book.py`**

For `workflow.get("review_evidence") == "review-ledger-v1"`, instantiate `ReviewLedgerManager` with a safe book-relative artifact reader. Read/validate ledger and call `resolve_all`. For every chapter already marked `reviewed`, require corresponding state `pass`; append deterministic validation errors otherwise.

Do not require all translated chapters to already have PASS; only claimed `reviewed` lifecycle state must be backed by current evidence. Coverage completeness belongs to #12/#21.

- [ ] **Step 6: Run focused tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_reviews tests.test_book_cli -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```text
workflow: gate reviewed state on current pass
```

---

### Task 4: Review CLI surface

**Files:**
- Create: `scripts/workflow_v2/review_cli.py`
- Modify: `scripts/book.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Create: `tests/test_workflow_v2_review_cli.py`

**Interfaces:**
- Consumes: `ReviewLedgerManager.record`, `resolve_all`, `accept_review`, `FilesystemStorage`, exact book-relative artifact reader.
- Produces:
  - `book.py review-record <book> <chapter> --outcome ... --session-id ... [--review-commit ...] [--json]`
  - `book.py reviews <book> [--json]`
  - `book.py accept-review <book> <chapter> [--json]`.

- [ ] **Step 1: Write failing end-to-end CLI tests**

Initialize a book with immutable install provenance, create a translation, set chapter state to translated, acquire a reviewer claim through the existing claim CLI, then require:

```text
review-record ... PASS --json -> exit 0, sequence/hash fields returned
reviews --json -> current state pass in canonical unit order
accept-review --json -> exit 0, progress becomes reviewed
edit translation
reviews --json -> state stale
accept-review --json -> non-zero and reviewed state is not falsely accepted
```

Also cover `CORRECTIONS_REQUIRED`, missing resolved workflow revision, foreign/non-reviewer claim, explicit `--review-commit`, and deterministic JSON key/unit/history ordering.

- [ ] **Step 2: Run CLI tests and verify RED**

```text
python -m unittest tests.test_workflow_v2_review_cli -v
```

Expected: argparse unknown commands / missing module.

- [ ] **Step 3: Implement CLI adapter**

Keep path/Git/argparse concerns out of `reviews.py`. `review_cli.py` loads metadata/progress with exact progress revision, creates an artifact reader rooted at the validated book directory, resolves best-effort `git rev-parse HEAD` only when `--review-commit` is omitted, maps review/schema/storage errors to `ReviewCliError`, and prints JSON with `sort_keys=True`.

Only a single positive chapter number is accepted for `review-record` and `accept-review` in #9; ranges remain out of scope.

- [ ] **Step 4: Register commands in `book.py`**

Import `ReviewCliError, register_review_commands`, call `register_review_commands(subparsers, repo_root())`, and catch `ReviewCliError` alongside existing `BookError`/`ClaimCliError`.

- [ ] **Step 5: Run CLI and regression tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_review_cli tests.test_workflow_v2_claim_cli tests.test_book_cli -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```text
workflow: expose machine review ledger commands
```

---

### Task 5: Execution-contract alignment, scope review, and full verification

**Files:**
- Modify: `docs/ORCHESTRATION.md`
- Modify: `tests/test_agent_contract.py`
- Verify: all `tests/`

**Interfaces:**
- Consumes: final #9 review CLI and promotion semantics.
- Produces: authoritative orchestration language requiring recorded current PASS before durable reviewed state.

- [ ] **Step 1: Write failing contract test**

Require exact executable surfaces and state boundary:

```python
for phrase in (
    "python scripts/book.py review-record",
    "python scripts/book.py reviews",
    "python scripts/book.py accept-review",
    "current pass",
    "stale",
):
    self.assertIn(phrase, orchestration.lower())
```

Also assert the contract does not claim Markdown audit files as authoritative review coverage.

- [ ] **Step 2: Run contract test and verify RED**

```text
python -m unittest tests.test_agent_contract -v
```

Expected: missing #9 command/evidence contract phrases.

- [ ] **Step 3: Update `docs/ORCHESTRATION.md`**

Document the sequence reviewer claim -> Reviewer outcome -> `review-record` -> `accept-review` -> release. State explicitly that PASS in chat is not durable evidence, changed artifacts make prior evidence stale, and a ledger-enabled reviewed chapter must resolve to current exact PASS. Preserve literary criteria in `docs/TRANSLATION.md` and keep #10/#12/#21 behavior out of this contract change.

- [ ] **Step 4: Run the complete suite**

```text
python -m unittest discover -s tests -v
```

Expected: all tests pass on Python 3.10 and 3.12.

- [ ] **Step 5: Inspect diff against `feature/workflow-v2-claims-cas`**

Confirm only #9 spec/plan, review schema/domain/CLI, targeted book initialization/validation, tests, and orchestration documentation changed. Verify no generated review report, status/resume, finalize, migration, GitHub backend, database/queue, or parallel scheduling implementation was introduced.

- [ ] **Step 6: Review production patches against acceptance criteria**

Manually inspect schema validation, claim expiry/ownership checks, exact-byte artifact hashing, sequence/supersession/correction-round logic, CAS error conversion, already-reviewed idempotence, legacy marker behavior, and deterministic CLI output. Add regression tests for any uncovered correctness risk before declaring completion.

- [ ] **Step 7: Open/update stacked PR and verify final CI**

Initially target `feature/workflow-v2-claims-cas` so the diff remains #9-only. PR body must state that #23/#24 must integrate first; after dependencies land, retarget to `refactor/workflow-engine-v2` and rerun the full Python 3.10/3.12 matrix before merge.

- [ ] **Step 8: Commit final contract alignment**

```text
docs: bind orchestration to review ledger evidence
```
