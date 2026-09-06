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
- concurrent glossary CAS failure without lost update;
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
- direct `WorkflowStateRepository` / `FilesystemStorage` access when a scenario must stop between two durable domain operations;
- deterministic claim/review fixtures with explicit timestamps and IDs when real-time CLI behavior would make a crash boundary nondeterministic;
- SHA snapshots used to prove read-only/idempotent behavior.

The harness does not own alternative business logic. It must use current schemas, repository methods, storage primitives, claim manager, review manager and corpus verifier rather than reimplementing them.

## Reliability invariants

The Phase 1 suite treats these as release-gate invariants:

1. Repository state, not the previous chat/process, determines recovery.
2. A live durable claim prevents a second session from dispatching conflicting work for that unit.
3. Claim expiry alone does not silently erase evidence; cleanup is explicit and auditable.
4. A crashed worker's claim remains the first recovery concern even if it already wrote translation/progress/review state.
5. Translation bytes without a lifecycle transition do not fabricate translated state.
6. Lifecycle `translated` without current PASS evidence resumes into review only after the crashed worker's claim is cleared.
7. Current PASS evidence without lifecycle promotion is recoverable through `accept_review` after the crashed reviewer claim is cleared; duplicate promotion does not corrupt state.
8. Changing source or translation bytes invalidates the PASS that was bound to the previous bytes.
9. A stale glossary writer cannot overwrite a newer glossary revision.
10. Invalid explicit source corpus state blocks literary work before dispatch.
11. `private_external` remains reproducible from a verified sealed extracted corpus after the source binary is absent and a new process starts.
12. Read-only status/resume never mutate durable state.
13. Retry-safe cleanup/release/promotion behavior must either be idempotent or return the current deterministic already-completed/not-owned result without corrupting state.

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

Recovery continuation is a separate deterministic fixture:

1. create a schema-valid translator claim for chapter 1 directly through the repository with fixed historical `claimed_at` / `expires_at` timestamps that are unambiguously expired;
2. run public `cleanup-claims`;
3. verify exactly one cleanup request and one completion event exist with `reason=lease_expired` and matching request linkage;
4. call `cleanup-claims` again and assert `results=[]` with no additional audit records;
5. start a fresh subprocess and verify `resume` returns `translate` for chapter 1.

No wall-clock sleeps are allowed.

### Scenario B — death after translation bytes, before progress update

Setup:

1. initialize chapter 1 as `extracted`;
2. create a deterministic active translator claim for chapter 1;
3. create non-empty `translated/...md` bytes directly, representing the claimed worker writing its artifact;
4. stop before the progress CAS transition.

Expected immediately after restart:

- fresh `resume` is blocked with `reason=unit_claimed`;
- lifecycle remains `extracted` because progress is authoritative;
- the translation artifact does not fabricate translated lifecycle state.

Recovery:

1. replace the fixture claim with an expired deterministic claim for the same crash boundary and clean it through the normal cleanup path;
2. fresh `resume` selects `translate`, not `review`, even though orphan translation bytes exist;
3. status/resume do not rewrite or auto-promote those bytes.

This documents the current recovery contract: orphaned translation bytes require the translator/orchestrator path to reconcile them explicitly; file presence alone is not lifecycle authority.

### Scenario C — death after translated lifecycle transition, before review

Setup:

1. create valid translation bytes under an active translator claim;
2. advance chapter lifecycle to `translated` through a versioned repository write;
3. stop before claim release and before any review evidence is written.

Expected immediately after restart:

- fresh status reports lifecycle translated and review missing;
- fresh `resume` is blocked by the surviving translator claim rather than dispatching a reviewer concurrently.

Recovery:

1. clear an expired equivalent claim through audited cleanup;
2. fresh `resume` selects `review` for the translated chapter;
3. no review evidence is fabricated.

### Scenario D — death after PASS record, before reviewed promotion

Setup:

1. create valid translated state and translation bytes;
2. create a matching reviewer claim;
3. record a current PASS in the ledger through the real review manager/CLI contract;
4. stop before lifecycle promotion and before claim release.

Expected immediately after restart:

- fresh status reports lifecycle translated + review pass + active reviewer claim;
- fresh `resume` is blocked by the surviving claim, preventing a second session from accepting/promoting while the reviewer still owns the unit.

Recovery:

1. clear an expired equivalent reviewer claim through audited cleanup;
2. fresh `resume` selects `accept_review`;
3. executing `accept-review` returns `changed=true` and promotes only the selected unit to `reviewed`;
4. executing `accept-review` again against unchanged current PASS returns `changed=false`, keeps the current progress revision/content stable, and does not append review-ledger records.

If this exact retry contract fails, the RED test identifies a production reliability gap owned by the review-promotion layer.

### Scenario E — translation changed after PASS

Setup:

1. reach a current PASS for a translated artifact;
2. ensure no active claim remains;
3. change exact translation bytes after PASS without adding new PASS evidence.

Expected recoverable case:

- with lifecycle still `translated`, status reports review stale and remains structurally valid;
- `resume` selects `review` for the stale artifact.

Expected fail-closed case:

1. restore original bytes, accept the PASS so lifecycle becomes `reviewed`, then modify translation bytes again;
2. status reports review stale and invalid because `reviewed` lacks current PASS evidence;
3. `resume` returns `blocked/preflight_failed`, never `complete`.

The tests reuse current review resolution; they do not calculate independent replacement review state.

### Scenario F — concurrent glossary changes through CAS

Setup:

1. initialize a book and read `glossary.md` through two independent `FilesystemStorage` clients, producing the same starting revision;
2. writer A changes glossary bytes and calls `write_if_version` with that observed revision;
3. writer B attempts a different glossary change using its stale starting revision.

Expected:

- writer A succeeds and returns a new revision;
- writer B receives `StorageVersionConflict`;
- final `glossary.md` bytes equal writer A’s complete content, with no partial/merged writer-B bytes;
- a third fresh storage client reads exactly the winning revision/content.

This directly covers #18's concurrent-glossary failure injection using the same storage CAS primitive intended for shared mutable state. It does not introduce a special glossary schema or automatic merge policy.

### Scenario G — corpus changes after a previously valid session

Two subcases:

- remove one extracted artifact from an explicit-source sealed book;
- modify one extracted artifact without updating its manifest hash.

Expected for both:

- fresh `status` reports invalid corpus/preflight evidence;
- fresh `resume` returns `blocked/preflight_failed`;
- no claim or workflow state mutation is created by resume.

### Scenario H — private source survives process restart without binary

Setup:

1. initialize through `book.py extract --private-source`;
2. assert canonical source binary is absent;
3. use new subprocesses for status/resume so no initialization Python object is reused.

Expected:

- corpus is verified with `storage_mode=private_external` and `source_attached=false`;
- source filename/size/SHA identity matches metadata/manifest;
- `resume` selects normal literary work from the sealed extracted corpus;
- repeated status/resume leave durable state byte-identical.

## Idempotence matrix

The Phase 1 suite explicitly checks these current contracts:

- `status`: two calls from unchanged state produce byte-for-byte equivalent canonical JSON and no durable writes;
- `resume`: two calls from unchanged state produce byte-for-byte equivalent canonical JSON and no durable writes;
- `cleanup-claims`: first cleanup of one expired claim produces the expected request/completion audit pair; second cleanup returns `results=[]` and creates no audit records;
- `release`: first owner release succeeds and creates one request/completion pair; second release for the now-absent claim fails deterministically with `ClaimConflict` / “no active claim” and creates no additional audit records;
- `accept-review`: first promotion with current PASS returns `changed=true`; the second call returns `changed=false`, does not alter progress content, and does not append ledger evidence.

The suite does not redefine idempotence as “always return exit code 0”. Retrying after an uncertain client outcome is safe when it cannot corrupt or duplicate durable state and reports the already-completed/absent condition deterministically.

## Production-change policy

Start with tests only.

For each reliability scenario:

1. add the smallest test that expresses the durable recovery invariant;
2. run it against the current integration-derived branch;
3. if it passes, retain it as missing release-gate coverage and make no production change;
4. if it fails for the intended reliability reason, identify the owning component and add the minimum production fix;
5. run the focused test, neighboring component tests and the complete standard suite;
6. commit test evidence and any GREEN fix in audit-friendly boundaries.

A failure caused only by a bad fixture/import/test assumption must be fixed in the test and re-run before any production change. A new test that already passes is valid reliability coverage; RED→GREEN is required only for behavior that is currently defective, not for adding tests around already correct behavior.

Potential production owners if genuine gaps are exposed:

- `scripts/workflow_v2/status.py` / `status_cli.py` — wrong resume/preflight composition;
- `scripts/workflow_v2/claims.py` / `claim_cli.py` — unsafe cleanup/retry semantics;
- `scripts/workflow_v2/reviews.py` / `review_cli.py` — PASS/promotion/idempotence gaps;
- `scripts/workflow_v2/repository.py` / filesystem storage implementation — CAS/lost-update gaps;
- `scripts/corpus.py` / source integrity helpers — fresh-session corpus verification gaps.

No unrelated refactor is permitted.

## Test determinism

- Use `tempfile.TemporaryDirectory` and repository-local fixtures.
- Use explicit historical timestamps for expired claims and deterministic IDs for asserted audit records; no sleeping.
- Compare structured/canonical JSON rather than unstable prose unless the human CLI output itself is the contract under test.
- When proving read-only behavior, snapshot SHA-256 of authoritative files before and after the operation.
- Do not depend on network access, GitHub Actions, external services or a real private source.
- Tests run under the repository’s normal `python -m unittest discover -s tests -v` suite.

## Expected implementation surface

Primary new file:

```text
tests/test_workflow_v2_reliability.py
```

Existing tests may receive only narrowly targeted regression assertions when a discovered defect belongs to an existing component boundary.

Production files are modified only when a failing reliability scenario proves a real missing invariant. No production file is pre-authorized merely because it is listed as a possible owner above.

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
