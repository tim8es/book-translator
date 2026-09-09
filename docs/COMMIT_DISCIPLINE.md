# Commit discipline and audit-friendly change boundaries

This document defines the recommended Git change boundaries for Book Translator development and book-state work. Git history is a **secondary audit trail**: authoritative workflow state remains in the versioned book documents and artifact identities described by `docs/ORCHESTRATION.md`.

This file is contributor/development guidance. It is not a Translator or Reviewer literary contract and does not need to be loaded into bounded worker context.

## Commit types

Use a narrow prefix that describes the atomic change. Examples:

```text
translate(chapter-000001): add canonical Russian translation
review(chapter-000001): record reviewed artifact state
fix(chapter-000001): correct mistranslation after review
state: reconcile durable workflow state
workflow: change workflow schema or orchestration behavior
build: refresh validated output artifact or manifest
```

Other conventional prefixes such as `test:`, `docs:`, `chore:`, and `refactor:` are appropriate for repository development when they describe the actual boundary.

## Stable change boundaries

A commit should be independently understandable and safely revertible.

- A translation-content commit should contain the intended translation artifact and only the directly related book-state/evidence mutation required by the same accepted transition.
- A review commit should identify the exact source/translation state it reviewed. When Git is the active backend, store the relevant review commit when the review API supports it; otherwise the durable state revision and artifact hashes remain sufficient authority.
- A correction should remain distinct from unrelated workflow implementation or schema work.
- Shared glossary/style reconciliation should be committed with the proposal/resolution or state mutation that explains it, not hidden inside unrelated chapter work.
- Workflow/schema changes should carry their tests and migration/compatibility updates in the same reviewable feature boundary.
- Build/output commits are optional according to repository policy. When committed, the output manifest must identify the authoritative source/workflow state and artifact hash used to produce the output.

**Do not mix translation content with workflow or schema changes** in the same atomic change. If both are necessary, land the workflow change first, re-read/revalidate durable state, then perform the literary change under the resulting workflow revision.

A bounded batch of mechanically identical changes may be grouped only when the batch has one purpose, one validation story, and no loss of artifact/state traceability. Do not create noisy per-command commits merely to make the history longer.

## Review and state identity

Git commit identity is useful provenance but is not the workflow database.

For durable acceptance, prefer the machine evidence already stored by Workflow v2:

- `translation_acceptance` binds source/translation SHA-256 values, claim identity, workflow revision, and shared-state revisions;
- review-ledger records bind exact source/translation hashes, workflow/review-contract revision, reviewer session, state revision, and optional review commit;
- `progress.json` lifecycle transitions use compare-and-swap revisions;
- output manifests bind generated artifacts to the relevant source/workflow state.

When a Git commit is unavailable or an operation is performed through another supported backend, the stored **state revision** and exact artifact hashes remain the authoritative identity. Never fabricate a commit SHA to make an audit record look complete.

## Revert and recovery

Reverting files is not by itself a valid workflow-state transition. After any revert or interrupted correction:

1. re-read current durable state;
2. validate structural and source-corpus integrity;
3. re-resolve translation acceptance and review evidence against current artifact hashes;
4. if a reviewed artifact changed, treat the old PASS as stale and return the unit to the appropriate translation/review boundary;
5. use the normal claim, acceptance, proposal-reconciliation, review, migration, or finalize recovery path instead of hand-editing derived reports;
6. regenerate derived reports/output from authoritative machine state after the state is valid again.

For failed multi-step operations, preserve the recovery journal/marker or conflicting durable bytes when the workflow says to fail closed. Do not delete recovery evidence merely to unblock a command.

## Branch and integration policy

For Workflow v2 development, keep implementation work off `main`:

1. create a feature/test/docs branch from the current integration or release base appropriate to the active project phase;
2. keep RED tests and their minimal GREEN implementation in reviewable boundaries;
3. integrate feature work through the designated integration branch while the epic is active;
4. use one final reviewed release PR from the integration candidate to `main` after release gates pass;
5. prefer a **squash** merge for that final integration PR when it can preserve the desired release history without losing required provenance.

Intermediate feature commits do not need to be replayed individually into `main`; PR history, CI evidence, durable workflow state, and the release commit provide the audit chain.

After a release has already landed, follow-up maintenance starts from the released `main` and uses a new isolated branch/PR. Do not rewrite published release history merely to restyle prior commits.

## Generated reports

`STATE.md`, `FINAL_QUALITY_GATES.md`, and `REVIEW_REPORT.md` are projections generated from authoritative machine state. They may be committed for human inspection, but they must not become the source of truth for lifecycle or review coverage. Regenerate them after authoritative state changes instead of editing them to manufacture completion evidence.
