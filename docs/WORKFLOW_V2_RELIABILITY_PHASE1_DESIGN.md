# Workflow v2 — Phase 1 reliability, failure injection and idempotence

Issue: #18
Branch: `test/workflow-v2-reliability`
Base: `refactor/workflow-engine-v2` at `b055e74c5694e000d047335820fd333f7bd74604`
PR target: `refactor/workflow-engine-v2`
Date: 2026-09-06

## Purpose

Close the Phase 1 reliability gate for Workflow v2 by proving that the already integrated #7–#11 state, concurrency, review, resume and source-integrity primitives compose safely across fresh sessions and realistic interruption boundaries.

The Phase 1 slice of #18 is intentionally a reliability test layer, not a new orchestration subsystem. It exercises real durable repository state through the public CLI and domain APIs, simulates interruption by stopping between durable writes, and verifies that a new session can determine the safe next action from repository state alone.

Later #18 slices will extend this same reliability suite after finalize (#12), EPUB build (#14), migrations (#16), GitHub backend (#17) and explicit parallel mode (#15) exist. This Phase 1 slice does not pre-implement those features.

## Scope

In scope now:

- fresh-session recovery after a durable claim is acquired and the worker disappears;
- deterministic blocking while a conflicting claim remains active;
- audited expired-claim cleanup and safe continuation afterward;
- recovery when translation bytes exist but lifecycle state was not advanced;
- recovery when lifecycle state is translated but review has not occurred;
- recovery when current PASS evidence exists but lifecycle promotion to `reviewed` did not occur;
- stale-review detection after reviewed translation bytes change;
- optimistic-concurrency failure for shared mutable repository state without lost update;
- fail-closed resume after an extracted corpus is removed or tampered;
- successful private-source resume after process restart without the original binary;
- explicit idempotence checks for read-only and safe retryable operations that already promise idempotent/repeat-safe behavior;
- standard Python test-suite execution, with no test depending on GitHub Actions as a runtime requirement.

Out of scope for this slice:

- interrupted/failed finalize and rollback (#12, then extend #18);
- build from incomplete state and output staleness (#14, then extend #18);
- workflow migration failure/rollback (#16, then extend #18);
- GitHub backend parity and API conflict injection (#17, then extend #18);
- shared-state proposal reconciliation in explicit parallel mode (#15, then extend #18);
- production-only fault-injection hooks introduced solely for tests;
- changes to `main`.

## Design choice

### Selected approach: end-to-end reliability harness over real repository state

Add a focused integration-style test module that creates a temporary repository/workspace, invokes the real `scripts/book.py` / `scripts/corpus.py` CLI where user-visible orchestration behavior matters, and uses Workflow v2 repository/domain APIs only to construct precise interruption boundaries that the public CLI cannot naturally stop at.

This gives each scenario two layers:

1. **Fault setup:** create exactly the durable subset that would remain if a session died at the named boundary.
2. **Fresh-session observation:** invoke `book.py status` / `book.py resume` in a new subprocess and assert the deterministic safe continuation or block reason.

This avoids duplicating unit tests already present for individual claims, CAS writes, review-ledger conflict resolution and corpus hashing. Existing component tests remain the proof that each primitive works in isolation; the new suite proves that the primitives compose correctly after interruption.

### Rejected approach: expand only existing component test files

Adding isolated cases to `test_workflow_v2_claims.py`, `test_workflow_v2_reviews.py` and `test_workflow_v2_status_cli.py` would increase coverage but would obscure the main #18 requirement: recovery behavior across durable boundaries and fresh sessions. Component files may receive a narrowly scoped regression case only if a RED reliability scenario exposes a bug owned by that component.

### Rejected approach: production fault-injection API

A generic crash/fault hook in production code is unnecessary for Phase 1. The relevant failure states can be constructed by stopping between existing durable operations. Test-only production hooks would widen the public/runtime surface without adding product value.

## Test harness boundary

Create `tests/test_workflow_v2_reliability.py` as the Phase 1 cross-cutting harness.

The harness owns:

- temporary repository creation;
- copying the current runtime scripts/package into that repository, following existing CLI test patterns;
- installation provenance fixture required by current book initialization;
- creation of a one- or two-chapter sealed book through real `book.py extract`;
- subprocess helpers for `book.py` and `corpus.py`;
- deterministic JSON parsing/assertion helpers;
- direct `WorkflowStateRepository` access when a scenario must stop between two durable domain operations;
- SHA snapshots used to prove read-only/idempotent behavior.

The harness does not own alternative business logic. It must use current schemas, repository methods, claim manager, review manager and corpus verifier rather than reimplementing them.

## Reliability invariants

The Phase 1 suite treats these as release-gate invariants:

1. Repository state, not the previous chat/process, determines recovery.
2. A live durable claim prevents a second session from dispatching conflicting work for that unit.
3. Claim expiry alone does not silently erase evidence; cleanup is explicit and auditable.
4. Translation bytes without a lifecycle transition do not fabricate translated state.
5. Lifecycle `translated` without current PASS evidence resumes into review, not completion.
6. Current PASS evidence without lifecycle promotion is recoverable through `accept_review`; duplicate promotion must not corrupt state.
7. Changing source or translation bytes invalidates the PASS that was bound to the previous bytes.
8. A stale writer cannot overwrite a newer shared-state revision.
9. Invalid explicit source corpus state blocks literary work before dispatch.
10. `private_external` remains reproducible from a verified sealed extracted corpus after the source binary is absent and a new process starts.
11. Read-only status/resume never mutate durable state.
12. Retry-safe cleanup/release/promotion behavior must either be idempotent or return a deterministic already-completed/not-owned result without corrupting state; tests assert the actual published contract rather than inventing silent success semantics.

## Failure scenarios

### Scenario A — session death after claim

Setup:

1. initialize a sealed book with chapter 1 in `extracted`;
2. acquire a translator claim for chapter 1 using the public claim command;
3. simulate death by performing no translation or release;
4. start a fresh subprocess and call `resume`.

Expected:

- `resume` returns `operation=blocked` and `reason=unit_claimed` for chapter 1;
- claim identity/session/expiry are reported from durable state;
- no translation/progress/review files are mutated by the fresh resume.

Recovery continuation:

- create an expired claim deterministically through the domain/repository fixture, or use a deterministic manager clock rather than sleeping;
- run cleanup;
- verify cleanup audit request/completion records exist with `lease_expired` reason;
- a fresh `resume` returns `translate` for chapter 1.

No wall-clock sleeps are allowed.

### Scenario B — death after translation bytes, before progress update

Setup:

1. initialize chapter 1 as `extracted`;
2. create non-empty `translated/...md` bytes directly, representing a worker that wrote the artifact and died before the progress CAS transition;
3. leave `progress.json` unchanged.

Expected:

- status lifecycle remains `extracted` because progress is authoritative for lifecycle;
- `resume` selects `translate`, not `review`;
- the existing translation artifact is not silently promoted or accepted as lifecycle state;
- status/resume are read-only.

This test documents the current recovery contract: orphaned translation bytes require the translator/orchestrator path to reconcile them explicitly; they are not auto-promoted from file presence.

### Scenario C — death after translated lifecycle transition, before review

Setup:

1. create valid translation bytes;
2. update chapter lifecycle to `translated` through versioned repository write;
3. leave review ledger without a current record.

Expected:

- fresh status reports lifecycle translated and review missing;
- fresh `resume` selects `review` for the chapter;
- no review evidence is fabricated.

### Scenario D — death after PASS record, before reviewed promotion

Setup:

1. create valid translated state and translation bytes;
2. acquire matching reviewer claim;
3. record a current PASS in the ledger;
4. stop before the separate lifecycle promotion/accept-review operation.

Expected:

- fresh status reports lifecycle translated + review pass;
- fresh `resume` selects `accept_review`;
- executing the existing accept-review operation promotes only the selected unit to reviewed using current PASS evidence;
- repeating the safe acceptance path returns the documented idempotent/already-reviewed result and does not append duplicate review evidence or rewrite unrelated state.

If current public CLI/API does not expose a repeat-safe accept path matching this invariant, the RED test identifies a production reliability gap owned by the review-promotion layer; the minimal fix belongs there.

### Scenario E — translation changed after PASS

Setup:

1. reach a current PASS for a translated/reviewed artifact;
2. change exact translation bytes after the PASS without adding new PASS evidence.

Expected:

- status reports review stale;
- a lifecycle already marked `reviewed` makes status invalid because reviewed state lacks current PASS evidence;
- `resume` is fail-closed rather than reporting complete;
- if lifecycle remains translated, `resume` selects review for the stale artifact.

The test must cover at least one fail-closed reviewed-state case and one recoverable translated-state case, reusing existing review resolution instead of recomputing hashes in the test.

### Scenario F — concurrent shared-state CAS

Setup:

1. two independent repository clients read the same mutable document revision (use `progress.json` as the Phase 1 shared-state representative unless a more appropriate existing mutable state document gives a clearer invariant);
2. writer A commits a valid update with the observed revision;
3. writer B attempts a different valid update using the stale revision.

Expected:

- writer A succeeds;
- writer B receives the existing version-conflict error;
- final durable content equals writer A’s complete update, with none of writer B’s changes;
- a fresh read parses under the normal schema.

This is a cross-session integration proof of the storage/repository CAS contract; it does not replace existing lower-level concurrency tests.

### Scenario G — corpus changes after a previously valid session

Two subcases:

- remove one extracted artifact from an explicit-source sealed book;
- modify one extracted artifact without updating its manifest hash.

Expected for both:

- fresh `status` reports invalid corpus/preflight evidence;
- fresh `resume` returns blocked/preflight_failed;
- no claim or workflow state mutation is created by resume.

### Scenario H — private source survives process restart without binary

Setup:

1. initialize through `book.py extract --private-source`;
2. assert canonical source binary is absent;
3. discard all Python objects from initialization by using a new subprocess for status/resume.

Expected:

- corpus is verified with `storage_mode=private_external` and `source_attached=false`;
- source filename/size/SHA identity matches metadata/manifest;
- `resume` selects normal literary work from the sealed extracted corpus;
- repeated status/resume leave durable state byte-identical.

## Idempotence matrix

The Phase 1 suite explicitly checks these operations:

- `status`: repeated calls produce deterministic JSON and no writes;
- `resume`: repeated calls from unchanged state produce deterministic operation/context and no writes;
- `cleanup-claims`: after expired claims are cleaned, a second call must not delete live state or create false cleanup completions for already absent claims; expected output follows the current manager contract;
- `release`: successful owner release followed by a second release must fail or report absence deterministically without recreating/deleting unrelated claims; exact assertion follows the existing public error contract;
- `accept_review`: repeat after successful current-PASS promotion must be non-destructive and must not duplicate ledger evidence.

The suite does not redefine all safe commands as “always return 0”. Idempotence means retrying after uncertain client outcome cannot corrupt or duplicate durable state; a deterministic already-done/not-found response is acceptable when that is the existing API contract.

## Production-change policy

Start with tests only.

For each reliability scenario:

1. add the smallest failing test that expresses the durable recovery invariant;
2. run it against the current integration-derived branch;
3. if it passes, retain it as missing release-gate coverage and make no production change;
4. if it fails for the intended reliability reason, identify the owning component and add the minimum production fix;
5. run the focused test, neighboring component tests and the complete standard suite;
6. commit the RED evidence and GREEN fix in audit-friendly boundaries.

A failure caused only by a bad fixture/import/test assumption must be fixed in the test and re-run before any production change.

Potential production owners if genuine gaps are exposed:

- `scripts/workflow_v2/status.py` / `status_cli.py` — wrong resume/preflight composition;
- `scripts/workflow_v2/claims.py` / `claim_cli.py` — unsafe cleanup/retry semantics;
- `scripts/workflow_v2/reviews.py` / `review_cli.py` — PASS/promotion/idempotence gaps;
- `scripts/workflow_v2/repository.py` / storage implementation — CAS/lost-update gaps;
- `scripts/corpus.py` / source integrity helpers — fresh-session corpus verification gaps.

No unrelated refactor is permitted.

## Test determinism

- Use `tempfile.TemporaryDirectory` and repository-local fixtures.
- Use explicit timestamps or injected clocks for lease boundaries; no sleeping.
- Use deterministic IDs where audit records are asserted.
- Compare structured JSON rather than unstable prose unless the human CLI output itself is the contract under test.
- When proving read-only behavior, snapshot SHA-256 of authoritative files before and after the operation.
- Do not depend on network access, GitHub Actions, external services or a real private source.
- Tests run under the repository’s normal `python -m unittest discover -s tests -v` suite.

## Expected implementation surface

Primary new file:

```text
tests/test_workflow_v2_reliability.py
```

Existing tests may receive only narrowly targeted regression assertions when a discovered defect belongs to an existing component boundary.

Production files are modified only when a RED reliability scenario proves a real missing invariant. No production file is pre-authorized merely because it is listed as a possible owner above.

Documentation:

```text
docs/WORKFLOW_V2_RELIABILITY_PHASE1_DESIGN.md
```

The implementation plan will be added only after this written design is explicitly reviewed and approved.

## Completion criteria for the Phase 1 slice

The Phase 1 #18 slice is ready to merge into `refactor/workflow-engine-v2` when:

- all scenarios A–H have deterministic automated coverage;
- the idempotence matrix is covered for the currently implemented operations;
- every discovered production reliability defect has its own demonstrated RED→GREEN cycle;
- full standard tests pass on all CI Python versions used by the repository;
- no test requires GitHub Actions to execute locally;
- the reliability PR contains no finalize/build/migration/backend/parallel implementation;
- branch remains based on/in sync with the current integration line;
- `main` is unchanged;
- review-thread/diff/CI audit is clean before Ready for review.

After merge, #18 remains open for later incremental slices tied to #12, #14, #16, #17 and #15. Phase 1 of the epic can then be treated as reliability-covered, while full #18 closes only after the later slices are integrated.
