# Workflow v2 Claims and CAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue #8 so concurrent sessions coordinate through durable per-chapter claims, expired leases are auditable, and filesystem shared-state writes use real compare-and-swap critical sections.

**Architecture:** Extend the #7 storage protocol with version-checked delete and serialize filesystem CAS mutations with OS advisory locks outside logical repository state. Add a backend-neutral claim domain service that derives canonical chapter unit IDs from validated progress state, acquires per-unit claim files atomically, rolls back failed ranges, and records append-only lifecycle audit events. Integrate the service through `scripts/book.py` while keeping GitHub/backend-specific behavior out of domain logic.

**Tech Stack:** Python 3.10+, standard library only (`fcntl`/`msvcrt`, `uuid`, `datetime`, `json`, `argparse`, `unittest`).

**Spec:** `docs/WORKFLOW_V2_CLAIMS_CAS_DESIGN.md`

## Global Constraints

- Repository state remains authoritative over chat history.
- No database, queue, backend service, mandatory GitHub Actions dependency, or new runtime package is introduced.
- Claims are one active file per canonical chapter unit under `.workflow/claims/`.
- Canonical unit IDs are `chapter-%06d`, derived from validated `progress.json` chapter numbers.
- Claim records include a collision-resistant `claim_id` UUID in addition to the approved spec fields; this prevents an ABA stale-release from deleting a newly recreated byte-identical claim.
- Expired claims remain conflicts until explicit cleanup records audit evidence.
- Range acquisition reports success only if every unit was acquired; partial acquisitions are rolled back with exact returned revisions.
- Filesystem CAS guarantees apply to writers using `StorageBackend`; direct out-of-band filesystem mutation is not serialized.
- Python 3.10 and 3.12 full suites must pass before the PR is review-ready.

---

### Task 1: Strong filesystem CAS and versioned delete

**Files:**
- Modify: `scripts/workflow_v2/storage.py`
- Modify: `scripts/workflow_v2/filesystem.py`
- Modify: `scripts/workflow_v2/repository.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Test: `tests/test_workflow_v2_storage.py`
- Test: `tests/test_workflow_v2_repository.py`

**Interfaces:**
- Consumes: existing `StoredValue`, `StorageVersionConflict`, SHA-256 content revisions, safe logical paths.
- Produces: `StorageBackend.delete_if_version(path: str, expected_version: str) -> None` and `WorkflowStateRepository.delete_if_version(path: str, schema: SchemaKind, expected_version: str) -> None`.

- [ ] **Step 1: Write failing storage tests**

Add tests that require version-checked deletion and a shared mutation mutex:

```python
def test_delete_if_version_rejects_stale_revision(self):
    storage = self.storage()
    old = storage.create_if_absent("claim.json", b"old")
    current = storage.write_if_version("claim.json", b"new", old)
    with self.assertRaises(StorageVersionConflict):
        storage.delete_if_version("claim.json", old)
    self.assertEqual(storage.read("claim.json").version, current)


def test_delete_if_version_removes_matching_revision(self):
    storage = self.storage()
    version = storage.create_if_absent("claim.json", b"claim")
    storage.delete_if_version("claim.json", version)
    with self.assertRaises(StorageNotFound):
        storage.read("claim.json")
```

Add a deterministic lock-contract test by injecting/patching the internal advisory-lock boundary and a multiprocessing stress test asserting that two backend writers starting from one expected revision never both report success.

- [ ] **Step 2: Run the focused tests and verify RED**

Run through CI/test runner:

```text
python -m unittest tests.test_workflow_v2_storage tests.test_workflow_v2_repository -v
```

Expected: failures because `delete_if_version` and the mutation-lock implementation do not exist yet.

- [ ] **Step 3: Implement the storage contract**

Extend the protocol:

```python
class StorageBackend(Protocol):
    ...
    def delete_if_version(self, path: str, expected_version: str) -> None:
        ...
```

Implement an internal advisory mutex keyed by normalized resolved root + logical path. POSIX uses `fcntl.flock`; Windows uses `msvcrt.locking`. Lock files live under a deterministic directory in `tempfile.gettempdir()`, contain no workflow state, and are not visible to `StorageBackend.list()`.

Wrap the complete read/check/replace and read/check/unlink critical sections:

```python
with self._mutation_lock(path):
    current = self.read(path)
    if current.version != expected_version:
        raise StorageVersionConflict(...)
    # replace or unlink while still holding the OS lock
```

- [ ] **Step 4: Add repository version-checked delete**

Validate the document before deletion and then delegate the exact revision to storage:

```python
def delete_if_version(self, path, schema, expected_version):
    loaded = self.read(path, schema)
    if loaded.version != expected_version:
        raise StorageVersionConflict(...)
    self.storage.delete_if_version(path, expected_version)
```

- [ ] **Step 5: Run focused tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_storage tests.test_workflow_v2_repository -v
```

Expected: PASS.

- [ ] **Step 6: Commit the independently reviewable storage change**

```text
workflow: add strong filesystem CAS deletion
```

---

### Task 2: Claim and claim-event schemas plus selector model

**Files:**
- Modify: `scripts/workflow_v2/schemas.py`
- Create: `scripts/workflow_v2/claims.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Test: `tests/test_workflow_v2_schemas.py`
- Create: `tests/test_workflow_v2_claims.py`

**Interfaces:**
- Consumes: `SchemaKind`, `WorkflowStateRepository`, `SCHEMA_VERSION`.
- Produces: `SchemaKind.CLAIM_EVENT`, `canonical_unit_id(number: int) -> str`, `resolve_selector(progress: Mapping[str, Any], selector: str) -> list[str]`, `ClaimManager` and claim-domain errors/results.

- [ ] **Step 1: Write failing schema/selector tests**

Require a claim UUID, canonical unit ID, nullable base commit, UTC timestamps, and action-specific claim events:

```python
claim = {
    "schema_version": 1,
    "claim_id": "0123456789abcdef0123456789abcdef",
    "unit_id": "chapter-000001",
    "role": "translator",
    "session_id": "session-a",
    "base_revision": "progress-rev",
    "base_commit": None,
    "workflow_revision": "workflow-rev",
    "claimed_at": "2026-09-05T12:00:00Z",
    "expires_at": "2026-09-05T13:00:00Z",
}
self.assertEqual(parse_document(SchemaKind.CLAIM, claim).data, claim)
```

Selector tests cover `1`, `1-3`, reversed ranges, missing chapters, duplicate progress chapter numbers, zero/negative/non-numeric selectors, and canonical `chapter-%06d` ordering.

- [ ] **Step 2: Run focused tests and verify RED**

```text
python -m unittest tests.test_workflow_v2_schemas tests.test_workflow_v2_claims -v
```

Expected: missing `CLAIM_EVENT`, `claim_id`, and claims module/API failures.

- [ ] **Step 3: Implement strict schema validation**

Add `CLAIM_EVENT`; validate `claim_id` as 32 lowercase hex characters, `unit_id` as `chapter-[0-9]{6}`, `base_commit` as null or non-empty string, timestamps as timezone-aware UTC RFC3339 values, and `expires_at > claimed_at`.

Request events accept `release_requested`/`cleanup_requested` and require exact claim snapshot/revision/reason. Completion events accept `released`/`cleaned` and require `request_event_id`.

- [ ] **Step 4: Implement canonical selector resolution**

```python
def canonical_unit_id(number: int) -> str:
    if type(number) is not int or number < 1:
        raise InvalidClaimSelector(...)
    return f"chapter-{number:06d}"
```

`resolve_selector` builds a number->unit map from progress, rejects duplicate numbers before mutation, parses only positive `N` or `N-M`, and verifies every integer in an inclusive range exists.

- [ ] **Step 5: Run schema/selector tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_schemas tests.test_workflow_v2_claims -v
```

Expected: PASS for schema and selector cases.

- [ ] **Step 6: Commit the schema/domain foundation**

```text
workflow: define durable claim identities and selectors
```

---

### Task 3: Claim acquisition, leases, rollback, release and cleanup audit

**Files:**
- Modify: `scripts/workflow_v2/claims.py`
- Modify: `scripts/workflow_v2/__init__.py`
- Test: `tests/test_workflow_v2_claims.py`

**Interfaces:**
- Consumes: repository create/read/delete, canonical unit IDs, `SchemaKind.CLAIM`, `SchemaKind.CLAIM_EVENT`.
- Produces: `ClaimManager.acquire(...)`, `list_active()`, `release(...)`, `cleanup_expired()`, structured lifecycle results, and errors `ClaimConflict`, `ClaimOwnershipError`, `ClaimRollbackError`, `ClaimAuditError`.

- [ ] **Step 1: Write failing acquisition tests**

Use an injected fixed UTC clock and deterministic UUID factory. Cover:

```python
first = manager.acquire(progress, "1", role="translator", session_id="a", ...)
with self.assertRaises(ClaimConflict):
    manager.acquire(progress, "1", role="reviewer", session_id="b", ...)
```

Also require expired-but-not-cleaned claims to remain conflicts, overlapping `1-3` vs `3-5` ranges to reject, and a forced `create_if_absent` race to roll back only revisions created by the failed batch.

- [ ] **Step 2: Run claim tests and verify RED**

```text
python -m unittest tests.test_workflow_v2_claims -v
```

Expected: missing lifecycle methods/results.

- [ ] **Step 3: Implement acquisition/listing**

Construct one claim document per unit with a unique `claim_id`, shared dispatch provenance, `claimed_at`, and `expires_at`. Preflight visible conflicts in canonical order, then call `repository.create` in canonical order. On `StorageAlreadyExists`, delete already-created batch members using their exact returned versions; if any rollback fails, raise `ClaimRollbackError` containing the unresolved paths.

- [ ] **Step 4: Write failing release/cleanup tests**

Require foreign-session release rejection, stale-version deletion protection, release request + completion audit, expired-only cleanup, live-claim preservation, cleanup request reason `lease_expired`, completion linkage, and deterministic unit ordering.

Include an ABA regression: delete/recreate the same logical unit with the same session/timestamps but a different `claim_id`; a stale lifecycle operation must not delete the replacement.

- [ ] **Step 5: Implement audited lifecycle operations**

For release/cleanup, persist a request event first, then version-checked delete, then completion event. A delete conflict leaves only the request event. A completion-event persistence failure after deletion raises `ClaimAuditError` and preserves the request event as evidence that the state mutation occurred after a recorded attempt.

Multi-unit release and cleanup process independent units in canonical order and return per-unit structured status; they do not claim range-atomic deletion semantics.

- [ ] **Step 6: Run all claim-domain tests and verify GREEN**

```text
python -m unittest tests.test_workflow_v2_claims tests.test_workflow_v2_schemas tests.test_workflow_v2_storage tests.test_workflow_v2_repository -v
```

Expected: PASS.

- [ ] **Step 7: Commit lifecycle coordination**

```text
workflow: add claims leases and lifecycle audit
```

---

### Task 4: `book.py` claim/list/release/cleanup CLI

**Files:**
- Modify: `scripts/book.py`
- Modify: `tests/test_book_cli.py`

**Interfaces:**
- Consumes: `ClaimManager`, validated metadata/progress documents plus exact progress revision.
- Produces:
  - `book.py claim <book> <selector> --role ... --session-id ... [--lease-seconds 3600] [--base-commit ...] [--json]`
  - `book.py claims <book> [--json]`
  - `book.py release <book> <selector> --session-id ... [--json]`
  - `book.py cleanup-claims <book> [--json]`

- [ ] **Step 1: Write failing CLI tests**

Create an isolated temporary book, then require:

```text
claim 1 -> exit 0
claim 1 from another session -> non-zero
claims --json -> stable canonical record list
release 1 wrong session -> non-zero and claim remains
release 1 owner -> exit 0 and audit files exist
cleanup-claims -> removes expired claims only
claim 1-3 -> reports success only after all three claims exist
```

Require invalid/missing workflow provenance to reject acquisition rather than fabricate `workflow_revision`.

- [ ] **Step 2: Run CLI tests and verify RED**

```text
python -m unittest tests.test_book_cli -v
```

Expected: argparse reports unknown claim commands / missing implementation.

- [ ] **Step 3: Implement CLI integration**

Add a helper that reads metadata/progress through `WorkflowStateRepository`, retaining `progress.version`. Resolve `workflow_revision` from `metadata.workflow.resolved_revision`, then `requested_ref`; otherwise raise `BookError`.

Resolve `base_commit` from explicit `--base-commit` first. If omitted, best-effort `git rev-parse HEAD` is allowed in the CLI layer only; failure records `null`.

Convert expected domain/storage/schema failures to `BookError`. JSON output uses `json.dumps(..., ensure_ascii=False, sort_keys=True)` and canonical unit ordering.

- [ ] **Step 4: Run CLI tests and verify GREEN**

```text
python -m unittest tests.test_book_cli tests.test_workflow_v2_claims -v
```

Expected: PASS.

- [ ] **Step 5: Commit the CLI surface**

```text
workflow: expose claim coordination commands
```

---

### Task 5: Execution-contract alignment and full regression verification

**Files:**
- Modify: `docs/ORCHESTRATION.md`
- Modify: `tests/test_agent_contract.py`
- Verify: all tests under `tests/`

**Interfaces:**
- Consumes: final claim CLI and lifecycle behavior.
- Produces: an orchestration contract that requires durable claim acquisition before dispatch and ownership-safe release/cleanup rather than manual scheduling.

- [ ] **Step 1: Write the failing contract test**

Require the orchestration contract to mention the executable claim gate and exact command surface without duplicating implementation details:

```python
for phrase in (
    "python scripts/book.py claim",
    "python scripts/book.py claims",
    "python scripts/book.py release",
    "python scripts/book.py cleanup-claims",
):
    self.assertIn(phrase, text)
```

- [ ] **Step 2: Run the contract test and verify RED**

```text
python -m unittest tests.test_agent_contract -v
```

Expected: missing claim-command contract phrases.

- [ ] **Step 3: Update `docs/ORCHESTRATION.md`**

Document that the orchestrator acquires the selected unit claim before literary dispatch, never treats expired ownership as free until cleanup, releases claims with matching session identity, and uses explicit cleanup for expired leases. Preserve the existing default sequential translation policy; #8 provides safe coordination primitives and does not enable #15 parallel mode.

- [ ] **Step 4: Run the complete suite**

```text
python -m unittest discover -s tests -v
```

Expected: all tests pass locally/CI with no regressions.

- [ ] **Step 5: Inspect the feature diff against `feature/workflow-v2-state-core`**

Confirm the diff contains only #8 design/plan, storage/CAS changes, claim domain/schema, CLI/tests, and targeted orchestration documentation. Verify no `docs/superpowers/`, database/queue/backend implementation, review ledger, status/resume, or parallel translation policy was introduced.

- [ ] **Step 6: Commit final contract alignment**

```text
docs: align orchestration with durable claims
```

- [ ] **Step 7: Open/update the stacked PR and verify CI on Python 3.10 and 3.12**

Initially target `feature/workflow-v2-state-core` so the PR diff is #8-only while #23 is pending. After #23 merges, retarget to `refactor/workflow-engine-v2` and re-run CI before integration.
