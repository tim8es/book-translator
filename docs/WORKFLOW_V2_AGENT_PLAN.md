# Workflow v2 — Agent Execution Plan

This file is a concise execution plan for AI agents working on Workflow v2.

## Branch policy

- Never develop Workflow v2 directly on `main`.
- Integration branch: `refactor/workflow-engine-v2`.
- Each issue uses its own feature/test/docs branch created from the current integration branch.
- Feature PRs target `refactor/workflow-engine-v2`.
- Final release is one squash PR from `refactor/workflow-engine-v2` to `main`.

## Parallelism policy

Target at most **3 implementation streams + 1 reliability/test stream**.

Do not start a dependent task before its required state/API is stable in the integration branch.

## Critical path

```text
#7 state/storage
 -> #8 claims/CAS
 -> #9 review ledger
 -> #10 status/resume
 -> #12 finalize
 -> integration dogfood
 -> final squash PR
```

Keep this path moving first.

## Execution waves

### Wave 0

Parallel:

- #7 State schemas and storage abstraction
- #13 Safe text patching
- #18 Test/fixture scaffolding

### Wave 1

After #7 merges:

- #8 Claims, leases and CAS
- #11 Source integrity and private-source mode
- #18 tests for #7/#8/#11

### Wave 2

After #8 merges:

- #9 Review ledger, hashes and stale-review detection
- #17 GitHub API storage backend
- #18 concurrency/backend tests

### Wave 3

After #9 merges:

- #10 Deterministic status/resume
- #21 Generated review report
- #14 EPUB build/validation/output manifest
- #18 review/status/output tests

### Wave 4

When #10 and #11 are ready:

- #12 Atomic finalize
- #16 Workflow/schema migrations
- finish #14 integration with finalize
- #18 finalize/migration/failure tests

### Wave 5

When #8 + #9 + #10 are ready:

- #15 Explicit parallel mode and proposal reconciliation
- #18 race/failure-injection tests

Wave 5 may overlap Wave 4 if workers are available.

### Wave 6 — release hardening

- #22 Commit discipline and audit-friendly boundaries
- #19 Remove core Actions dependency and align docs
- #18 full reliability gate
- full dogfood: resume -> work -> review -> restart -> resume -> finalize -> EPUB

## Important dependency rules

- #17 depends on #7 + #8; it does **not** need to wait for #9.
- #14 may start after #9; only final integration with `finalize` waits for #12.
- #12 consumes authoritative review ledger state from #9; it must not depend on generated Markdown reports from #21.
- #21 and #12 can run in parallel after #9, subject to #12 also having #10/#11 ready.
- #15 depends on #8 + #9 + #10; it does not need to wait for EPUB/finalize.
- #16 should wait until schemas from #9 and #11 are stable.
- #19 should be finalized late, after real CLI/domain behavior is stable.

## Merge order target

Preferred integration order, not necessarily task start order:

1. #7
2. #13
3. #8
4. #11
5. #17
6. #9
7. #10
8. #21
9. #14
10. #12
11. #16
12. #15
13. #22
14. #19
15. close #18 only after the full release reliability gate

## Agent rules

- Read issue scope and acceptance criteria before coding.
- Branch from the latest `refactor/workflow-engine-v2`.
- Do not invent duplicate state/concurrency/review mechanisms in feature branches.
- Reuse the APIs introduced by upstream issues.
- Keep feature PRs focused; do not mix unrelated schema/workflow/content changes.
- Add or update tests with each implementation task; #18 is the cross-cutting release gate, not a substitute for task-local tests.
- Rebase/update from the integration branch before final PR review when upstream dependencies changed.
- Do not merge directly to `main`.

## Release gate

Do not open the final PR to `main` until all are true:

- P0/P1 invariants are covered by tests.
- `refactor/workflow-engine-v2` is green.
- no mandatory GitHub Action is required for core translate/review/resume/finalize/build flow.
- review completion is derived from machine-readable ledger state.
- private-source mode works without committing copyrighted source binaries.
- filesystem and GitHub API execution paths satisfy the same domain invariants.
- full restart/resume/finalize/EPUB dogfood succeeds.

Tracking epic: #20.
