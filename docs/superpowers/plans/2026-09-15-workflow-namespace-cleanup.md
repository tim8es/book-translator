# Workflow Namespace Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the current-only workflow consolidation by removing version-era naming and legacy schema extension machinery without changing supported workflow behavior.

**Architecture:** Keep `scripts/book.py` and `scripts/corpus.py` as the public CLI facade for this PR, but rename the sole internal runtime package from `scripts/workflow_v2/` to `scripts/workflow/`. Fold source/parallel schema validation into the canonical schema module, remove legacy parsing flags/markers, and update tests/docs to describe one current runtime.

**Tech Stack:** Python 3.10/3.12, unittest, GitHub Actions.

**Spec:** `docs/ARCHITECTURE.md`

## Global Constraints

- Current workflow behavior must remain unchanged.
- No legacy runtime or automatic legacy schema normalization is supported.
- `main` is not changed until exact-head Python 3.10/3.12 CI is green.
- Historical PR #36 is not touched.

---

### Task 1: Architectural RED contract

**Files:**
- Modify: `tests/test_architecture_consolidation.py`

**Interfaces:**
- Consumes: repository tree and runtime source files.
- Produces: failing assertions that forbid `scripts/workflow_v2`, legacy schema symbols, and import-time validator installation.

- [ ] Add tests requiring `scripts/workflow/` and forbidding `scripts/workflow_v2/`.
- [ ] Add tests forbidding `LEGACY_COMPATIBLE_KINDS`, `ParsedDocument.legacy`, `allow_legacy`, and `install_*_schema_extensions` in current runtime source.
- [ ] Run CI and confirm RED for the expected architecture-only reasons.

### Task 2: Rename the sole runtime package

**Files:**
- Move: `scripts/workflow_v2/*` -> `scripts/workflow/*`
- Modify: `scripts/book.py`, `scripts/corpus.py`, docs, tests and manifest references that import or name the package.

**Interfaces:**
- Consumes: existing public internal API from `workflow_v2.__init__`.
- Produces: same API under `workflow`.

- [ ] Move runtime files without content changes other than namespace/docstring updates.
- [ ] Replace imports/references from `workflow_v2` to `workflow`.
- [ ] Rename test files/classes where `WorkflowV2` is only historical naming.
- [ ] Run focused import/CLI tests and then full suite.

### Task 3: Remove legacy schema API and extension monkey-patching

**Files:**
- Modify: `scripts/workflow/schemas.py`, `scripts/workflow/__init__.py`
- Delete after folding logic: `scripts/workflow/source_schema.py`, `scripts/workflow/parallel_schema.py`
- Modify: schema/repository tests.

**Interfaces:**
- Consumes: current schema validation behavior for metadata, progress, claim, source manifest and coordination locks.
- Produces: one explicit validator table defined by `schemas.py`, with no import-time mutation or legacy parsing mode.

- [ ] Fold explicit-source validation directly into metadata/source-manifest validators.
- [ ] Fold translation-acceptance/shared-state/coordination validation directly into canonical validators.
- [ ] Remove `LEGACY_COMPATIBLE_KINDS`, legacy normalization, `ParsedDocument.legacy`, and `allow_legacy` behavior.
- [ ] Update repository and schema tests to current-only API.
- [ ] Run focused schema/repository tests, then full suite.

### Task 4: Verify and merge readiness

**Files:**
- Modify: `docs/ARCHITECTURE.md` and any canonical references still naming Workflow v2.

**Interfaces:**
- Produces: one understandable production runtime namespace and an exact-head verification record.

- [ ] Search current branch for active `workflow_v2`, `WorkflowV2`, legacy schema API, and schema-extension installer references; remove current-runtime leftovers.
- [ ] Run full GitHub Actions matrix on Python 3.10/3.12.
- [ ] Verify representative resume -> review -> finalize -> EPUB dogfood is green.
- [ ] Review diff for behavior changes; do not merge unless exact-head matrix is fully green.
