# Workflow v2 Generated Review Report — Minimal Plan

Issue: #21
Branch: `feature/workflow-v2-review-report`
Base: `refactor/workflow-engine-v2` at `80c3fce90ac812c78aaf45a892ad4f648c7469ef`
Target: `refactor/workflow-engine-v2`

## Goal

Generate one deterministic `REVIEW_REPORT.md` from authoritative Workflow v2 review-ledger/progress/artifact state so review coverage no longer depends on handwritten `REVIEW_AUDIT_<range>.md` files.

## Design boundary

- Add backend-neutral `workflow_v2.review_report` snapshot/rendering logic.
- Reuse `ReviewLedgerManager.resolve_all()` for current review state and the validated `review-ledger.json` history for audit details.
- Add `book.py review-report <slug>` through the existing review CLI adapter.
- Default mode atomically updates `books/<slug>/REVIEW_REPORT.md` and prints a concise summary.
- `--json` prints deterministic machine-readable snapshot data and does not write the Markdown report.
- `REVIEW_REPORT.md` is generated evidence, never authoritative state. Ledger, progress, current artifact bytes and workflow revision remain authoritative.
- Future #12 finalize must consume the same snapshot builder directly; #21 does not implement finalize.
- Handwritten `REVIEW_AUDIT_*` files are neither read nor required.

## Record classification

For each unit, history records are preserved in ledger sequence order.

- `current`: the record selected by `ReviewLedgerManager.resolve_unit()` for current artifact/workflow identity.
- `stale`: record identity no longer matches current source/translation/workflow/contract identity.
- `superseded`: identity still matches but a later record for the same unit is current.
- `duplicate`: a later record repeats the same unit + source hash + translation hash + workflow revision + review-contract revision + outcome as an earlier record. It remains visible in history but does not add coverage.
- current `CORRECTIONS_REQUIRED` is reported as failed coverage (`corrections_required`).

## Acceptance criteria

1. `book.py review-report <slug>` creates deterministic `books/<slug>/REVIEW_REPORT.md` for unchanged state.
2. `book.py review-report <slug> --json` emits deterministic JSON and does not mutate the report file.
3. Summary contains total units and counts for `pass`, `corrections_required`, `missing`, `stale`, and `untranslated`, plus PASS coverage percentage/count.
4. 100% PASS coverage is computable entirely from ledger/progress/current artifact data; handwritten audit files are ignored.
5. Every unit row contains current state, source SHA-256, current translation SHA-256 when present, and current review revision/commit when a current record exists.
6. Stale records are visibly marked and never count toward PASS coverage.
7. Current `CORRECTIONS_REQUIRED` is visibly marked and never counts toward PASS coverage.
8. Superseded and duplicate records remain visible in per-unit history; duplicate records are explicitly linked to the earlier equivalent record.
9. Missing/untranslated units remain visible even with no review history.
10. Invalid/missing ledger or unreadable required artifacts fail closed with expected CLI error and no partial report replacement.
11. Generated Markdown contains no timestamps or nondeterministic data not already present in ledger state.
12. Snapshot/render functions are reusable by #12 finalize without parsing Markdown.
13. Full Python 3.10/3.12 CI matrix passes; branch remains `behind_by=0`; `main` remains unchanged; feature branch is preserved.

## TDD slices

### Slice 1 — snapshot semantics

RED tests for mixed states (`pass`, `corrections_required`, `missing`, `stale`, `untranslated`), coverage counts, current record metadata, stale exclusion, superseded history and duplicate detection.

GREEN: implement `review_report.py` snapshot builder using existing review resolution and validated ledger state.

### Slice 2 — deterministic Markdown/JSON

RED tests for stable Markdown bytes/order, explicit history classifications, no handwritten audit dependency, and deterministic JSON-serializable snapshot.

GREEN: add deterministic renderer(s), no wall-clock fields.

### Slice 3 — CLI generation

RED tests for `book.py review-report`, canonical file path, idempotent identical rerun, `--json` read-only behavior, and fail-closed invalid ledger behavior.

GREEN: wire the command through `review_cli.py` and safe generated-file replacement.

### Verification

Run full CI on Python 3.10/3.12, audit diff + PR comments/reviews/threads + branch ancestry + `main`, update PR evidence, mark Ready only after all checks, then merge only to `refactor/workflow-engine-v2` with expected-head guard.
