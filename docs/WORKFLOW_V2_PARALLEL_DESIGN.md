# Workflow v2 explicit parallel mode — minimal slice

Status: approved architecture; implementation target is issue #15.

## Decision

Sequential orchestration remains the unconditional default. Parallel planning is enabled only for the current invocation with `resume --parallel N`, where `N > 1`.

No durable per-book parallelism setting is introduced.

## Durable shared-state snapshot

`glossary.md` and `style-guide.md` remain shared orchestrator-owned state. A status snapshot records their backend versions as `state_revisions.glossary` and `state_revisions.style_guide`.

A parallel worker claim persists the frozen shared-state revisions it received at dispatch time:

```json
{
  "shared_state_revisions": {
    "glossary": "<backend version>",
    "style_guide": "<backend version>"
  }
}
```

The field is optional for legacy/sequential claims so existing sequential behavior and stored claims remain valid. When present, both keys are required and non-empty.

## Parallel planning

`StatusResolver.resume(status)` keeps current sequential semantics.

`StatusResolver.resume(status, parallel=N)` with `N > 1` returns a bounded parallel batch of at most `N` actionable units. It never emits the same unit twice and skips units that already have an active claim. Preflight, migration, and finalization continue to block/override worker dispatch exactly as in sequential mode.

The returned worker context carries the same frozen `state_revisions`, including glossary/style versions, so the orchestrator can pass those exact revisions into `ClaimManager.acquire(..., shared_state_revisions=...)`.

## Shared-state writes

Workers do not write `glossary.md` or `style-guide.md` directly. Shared-state mutation remains an orchestrator/single-writer responsibility. Proposal reconciliation under `.workflow/proposals/` is a later slice of #15; this slice establishes the invocation boundary and durable shared-state snapshot required before proposal acceptance can be made stale-safe.

## Safety invariants

1. No `--parallel` means current sequential behavior.
2. Parallel batches contain only disjoint unclaimed units.
3. Every parallel worker context exposes frozen glossary/style revisions.
4. A durable claim can record the exact frozen glossary/style revisions used by that worker.
5. No new code path gives translators/reviewers direct shared-state write ownership.

## TDD slice

RED tests cover:
- glossary/style revisions in status/context;
- explicit `parallel=N` batch planning and skip-claimed behavior;
- sequential compatibility;
- durable claim persistence and schema validation of frozen shared-state revisions;
- CLI parsing for `resume --parallel N`.

GREEN is limited to the minimum changes required for those tests.