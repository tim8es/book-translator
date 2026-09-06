# Workflow v2 Phase 1 Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Phase 1 recovery, failure-injection and idempotence coverage for the already integrated Workflow v2 #7–#11 behavior, fixing production code only if a reliability scenario proves a real defect.

**Architecture:** Add one cross-cutting integration harness, `tests/test_workflow_v2_reliability.py`, that creates a temporary repository, invokes the real `book.py`/`corpus.py` CLIs in fresh subprocesses, and uses the existing repository/domain APIs only to create exact crash-boundary durable states. Existing component tests remain unchanged unless a newly demonstrated defect requires a narrowly owned regression assertion. No production fault-injection API is introduced.

**Tech Stack:** Python 3.10/3.12, `unittest`, `tempfile`, `subprocess`, `FilesystemStorage`, `WorkflowStateRepository`, `ClaimManager`, `ReviewLedgerManager`, existing Workflow v2 schemas/CLI.

**Spec:** `docs/WORKFLOW_V2_RELIABILITY_PHASE1_DESIGN.md`

## Global Constraints

- Work only on `test/workflow-v2-reliability`, based on `refactor/workflow-engine-v2` at `b055e74c5694e000d047335820fd333f7bd74604`.
- Do not modify `main`.
- Do not delete branches.
- Phase 1 scope is only #7–#11 reliability; do not implement finalize (#12), build (#14), migrations (#16), GitHub backend (#17), or parallel mode (#15).
- Start with tests only. Production code changes are allowed only after a reliability test fails for the intended invariant rather than fixture/test error.
- No sleeps, network access, external services, real private source, or production-only fault hooks.
- Standard execution remains `python -m unittest discover -s tests -v`; GitHub Actions is CI evidence only, not a runtime dependency.
- Read-only `status`/`resume` must be proven non-mutating.
- Idempotence means safe retry without duplicate/corrupt durable state; deterministic already-done/not-found failure is acceptable where that is the existing contract.

---

## File Map

### Create

- `tests/test_workflow_v2_reliability.py`
  - temporary repository/CLI harness;
  - deterministic crash-state fixtures;
  - scenarios A–H from the approved spec;
  - Phase 1 idempotence matrix.

### Modify only if a RED proves a defect

- `scripts/workflow_v2/status.py` or `status_cli.py` — incorrect fresh-session resume/preflight composition only.
- `scripts/workflow_v2/claims.py` or `claim_cli.py` — unsafe cleanup/retry behavior only.
- `scripts/workflow_v2/reviews.py` or `review_cli.py` — PASS promotion/idempotence defect only.
- `scripts/workflow_v2/repository.py` or `filesystem.py` — proven CAS/lost-update defect only.
- `scripts/corpus.py` or source-integrity helpers — proven fresh-session corpus defect only.

---

### Task 1: Build the deterministic reliability harness and claim-crash recovery

**Files:**
- Create: `tests/test_workflow_v2_reliability.py`

**Interfaces:**
- Consumes public commands: `book.py extract`, `claim`, `cleanup-claims`, `status`, `resume`.
- Consumes domain APIs: `FilesystemStorage`, `WorkflowStateRepository`, `SchemaKind`.
- Produces helpers reused by Tasks 2–4:
  - `run_book(*args, expect=0)`
  - `run_corpus(*args, expect=0)`
  - `initialize_book(private=False)`
  - `canonical_json(result)`
  - `book_storage()` / `book_repository()`
  - `authoritative_snapshot()`
  - `write_claim(...)` for fixed timestamps and IDs.

- [ ] **Step 1: Create the harness skeleton and one live-claim crash test**

Use the same runtime-copy pattern as `tests/test_workflow_v2_status_cli.py`: copy `scripts/book.py`, `scripts/corpus.py`, and the entire `scripts/workflow_v2/` package into a temporary repository; write `.book-translator-install.json` with resolved revision `0123456789abcdef`.

The first test must initialize a one-chapter sealed Markdown book, acquire a translator claim through the public CLI, call `resume --json` in a fresh subprocess, and assert:

```python
self.assertEqual(payload["operation"], "blocked")
self.assertEqual(payload["reason"], "unit_claimed")
self.assertEqual(payload["unit_id"], "chapter-000001")
self.assertEqual(payload["claim"]["session_id"], "translator-crashed")
```

Snapshot `metadata.json`, `progress.json`, `review-ledger.json`, `source-manifest.json`, and all current claim/audit paths before and after `resume`; assert no durable content changed.

- [ ] **Step 2: Run the focused test against current code**

Run through the PR CI harness by committing the test-only change and opening/using a draft PR to `refactor/workflow-engine-v2` if no push workflow exists.

Expected outcome: PASS is acceptable because this task primarily adds missing reliability coverage. If it fails, inspect the exact failure before touching production code.

- [ ] **Step 3: Add deterministic expired-claim cleanup recovery**

Create a schema-valid claim directly through `WorkflowStateRepository.create()` with fixed values:

```python
{
    "schema_version": 1,
    "claim_id": "1" * 32,
    "unit_id": "chapter-000001",
    "role": "translator",
    "session_id": "translator-crashed",
    "base_revision": progress_revision,
    "base_commit": None,
    "workflow_revision": "0123456789abcdef",
    "claimed_at": "2020-01-01T00:00:00Z",
    "expires_at": "2020-01-01T00:01:00Z",
}
```

Then call public `cleanup-claims sample --json` and assert one result with `status=cleaned`. Read `.workflow/claim-events/*` through the repository and assert exactly one `cleanup_requested` record with `reason=lease_expired` and one `cleaned` completion linked by `request_event_id`.

Call cleanup a second time and assert canonical JSON is exactly `{"results": []}` and the claim-event path set/content is unchanged. Fresh `resume` must return `translate` for chapter 1.

- [ ] **Step 4: Add owner-release retry safety**

Acquire a live claim through the CLI, release it through the CLI, snapshot `.workflow/claim-events`, then call release again with the same selector/session and expect exit code 1 with deterministic `no active claim`/claim-conflict semantics. Assert the second attempt creates no audit records and does not recreate the claim.

- [ ] **Step 5: Commit Task 1 test coverage**

Commit only `tests/test_workflow_v2_reliability.py` unless a genuine defect required a separately demonstrated production fix.

Suggested message: `test: cover phase1 claim crash recovery`

---

### Task 2: Cover translation and review crash boundaries

**Files:**
- Modify: `tests/test_workflow_v2_reliability.py`
- Modify production review/status owner only if a RED proves a defect.

**Interfaces:**
- Uses Task 1 harness.
- Consumes: `WorkflowStateRepository.read/write_if_version`, `ReviewLedgerManager`, `SchemaKind.PROGRESS`, `SchemaKind.CLAIM`.
- Produces recovery coverage for crash boundaries B–E.

- [ ] **Step 1: Add crash after translation bytes but before progress CAS**

Initialize chapter 1 as `extracted`. Create an active translator claim fixture. Write a non-empty translation artifact directly to the chapter’s declared `translation_path` without changing `progress.json`.

Fresh `status --json` must still report lifecycle `extracted`. Fresh `resume --json` must first return `blocked/unit_claimed`.

Replace the active fixture with an expired equivalent claim, run audited cleanup, then call fresh `resume --json`; assert `operation=translate`, never `review`. Snapshot progress/review ledger around status/resume to prove no auto-promotion.

- [ ] **Step 2: Add crash after lifecycle translated but before review**

Initialize the book, create translation bytes, create active translator claim, read `progress.json` with repository version, set chapter status to `translated`, and persist with `write_if_version()`.

Fresh status must report `translated=1` and `reviews.missing=1`; fresh resume must be blocked by the surviving translator claim. After deterministic expired-claim cleanup, fresh resume must return `operation=review` and no review-ledger record may have been fabricated.

- [ ] **Step 3: Add crash after PASS ledger write but before reviewed promotion**

Build a translated chapter, create a matching reviewer claim, and record PASS using the real public `review-record` command with session `reviewer-crashed`. Stop before `accept-review` or claim release.

Fresh status must show lifecycle `translated`, review `pass`, and the active reviewer claim. Fresh resume must return `blocked/unit_claimed`.

For recovery, create the same durable PASS state with an expired reviewer claim (or replace only the claim while preserving ledger/progress), clean it, then assert fresh resume returns `accept_review`.

Run `accept-review sample 1 --json` twice. Assert first result has `changed=true`; second has `changed=false`. Snapshot `review-ledger.json` after first acceptance and assert the second call does not change it. Assert the second call leaves `progress.json` content and revision stable.

- [ ] **Step 4: Add stale-review recovery and fail-closed cases**

Case 1: leave lifecycle `translated` with current PASS, remove any active claim, change translation bytes, then assert status review state is `stale` and fresh resume returns `review`.

Case 2: restore original bytes, accept the PASS so lifecycle becomes `reviewed`, then modify translation bytes again. Assert status is invalid with an error containing `reviewed without current PASS evidence`, and resume returns `blocked/preflight_failed`, never `complete`.

- [ ] **Step 5: Run focused + neighboring review/status tests**

CI/test selection must include at minimum:

```text
test_workflow_v2_reliability.py
test_workflow_v2_reviews.py
test_workflow_v2_review_cli.py
test_workflow_v2_status.py
test_workflow_v2_status_cli.py
```

If the new tests pass, make no production change. If an intended invariant fails, first capture the failing test/run as RED evidence, then apply the minimum owner fix and rerun these files before full suite.

- [ ] **Step 6: Commit Task 2**

Suggested message if tests only: `test: cover translation and review crash recovery`

If a production defect is found, use two audit-friendly commits: one RED test commit and one minimum GREEN fix commit.

---

### Task 3: Cover shared-state CAS, corpus failures, and private-source restart

**Files:**
- Modify: `tests/test_workflow_v2_reliability.py`
- Modify production storage/corpus owner only if a RED proves a defect.

**Interfaces:**
- Uses Task 1 harness.
- Consumes raw `FilesystemStorage.read/write_if_version` for `glossary.md` CAS.
- Consumes existing `status`/`resume` corpus preflight and `private_external` semantics.

- [ ] **Step 1: Add concurrent glossary CAS test**

Create two independent `FilesystemStorage(book_dir)` instances and read `glossary.md` from both, asserting they observe the same initial version.

Writer A calls:

```python
new_version = storage_a.write_if_version(
    "glossary.md",
    b"# Glossary\n\nalpha = A\n",
    first_a.version,
)
```

Writer B calls `write_if_version()` with its stale `first_b.version` and different bytes, and must raise `StorageVersionConflict`.

A third fresh storage instance must read exactly writer A’s bytes and `new_version`; no writer-B bytes may appear.

- [ ] **Step 2: Add missing extracted artifact fail-closed test**

Initialize an explicit-source book, verify baseline `status.valid == true`, delete the chapter’s extracted file, then call fresh status/resume.

Assert status corpus state is `invalid`; resume returns `blocked/preflight_failed`; no `.workflow/claims/` entry is created by resume.

- [ ] **Step 3: Add tampered extracted artifact fail-closed test**

Initialize another explicit-source book, append deterministic bytes to the extracted artifact without changing `source-manifest.json`, then assert fresh status is invalid with a hash-mismatch error and resume is `blocked/preflight_failed`. Snapshot claims/progress/review ledger before/after resume to prove no mutation.

- [ ] **Step 4: Add private-source process-restart test**

Initialize with `book.py extract ... --private-source`. Assert the canonical source path under `books/sample/source/` is absent. Capture source identity from `metadata.json`.

Use two entirely new subprocesses for `status --json` and `resume --json`. Assert:

```python
self.assertEqual(status["corpus"]["state"], "verified")
self.assertEqual(status["corpus"]["storage_mode"], "private_external")
self.assertFalse(status["corpus"]["source_attached"])
self.assertEqual(status["corpus"]["source_sha256"], metadata["source"]["sha256"])
self.assertEqual(status["corpus"]["source_size_bytes"], metadata["source"]["size_bytes"])
self.assertEqual(resume["operation"], "translate")
```

Run status and resume twice each from unchanged state and assert canonical JSON equality plus byte-identical authoritative snapshots.

- [ ] **Step 5: Run focused + neighboring corpus/storage tests**

Include at minimum:

```text
test_workflow_v2_reliability.py
test_workflow_v2_repository.py
test_workflow_v2_storage.py
test_workflow_v2_private_source.py
test_corpus_cli.py
test_workflow_v2_status_cli.py
```

Capture a RED only for genuine invariant failures. Fixture mistakes must be corrected before production changes.

- [ ] **Step 6: Commit Task 3**

Suggested message if tests only: `test: cover cas corpus and private-source recovery`

---

### Task 4: Full verification, PR audit, and Phase 1 release-gate evidence

**Files:**
- Modify: PR body only; no repository files unless verification exposes a real issue.

**Interfaces:**
- Consumes complete Task 1–3 suite.
- Produces merge-ready Phase 1 #18 reliability evidence while leaving #18 open for later slices.

- [ ] **Step 1: Run complete standard suite on supported Python matrix**

Require successful CI for the final head on Python 3.10 and 3.12 using the repository’s existing `python -m unittest discover -s tests -v` workflow.

Record total test count and run ID(s).

- [ ] **Step 2: Audit final branch diff**

Compare `refactor/workflow-engine-v2` to `test/workflow-v2-reliability` and verify:

- branch is not behind integration;
- new changes are limited to the design, plan, reliability tests, and any production file with a documented RED→GREEN defect;
- no finalize/build/migration/backend/parallel implementation entered the branch;
- no private/copyrighted source fixture was committed.

- [ ] **Step 3: Audit PR discussion/reviews/threads**

Fetch PR comments, submitted reviews and inline review threads. Resolve any blocking finding before Ready for review. Re-run CI after any code change.

- [ ] **Step 4: Update PR body with evidence**

Include:

- base/head SHA;
- scenarios A–H coverage mapping;
- idempotence matrix results;
- any RED run and matching GREEN fix run;
- final full-suite matrix run/test count;
- changed files;
- statement that #18 remains open for later #12/#14/#16/#17/#15 extensions;
- statement that `main` was not changed and no branch was deleted.

- [ ] **Step 5: Mark Ready for review only after all gates are green**

Do not merge the PR without the project’s integration permission gate. Do not merge to `main`.

---

## Self-Review Checklist

Before execution, verify:

- Every approved scenario A–H maps to an explicit task step.
- Idempotence covers status, resume, cleanup, release and accept-review.
- Crash scenarios B–D correctly preserve the worker/reviewer claim before cleanup.
- Concurrent shared-state injection uses `glossary.md`, not a surrogate state document.
- No step requires sleeping or wall-clock timing.
- No step pre-authorizes a production change without a genuine RED.
- Later #18 scopes remain explicitly deferred.
- All named APIs/commands exist in the current integration-derived branch.
