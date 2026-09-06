# Workflow v2 EPUB Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic first-class EPUB build/validation, output manifest and stale-output detection while preserving Markdown build behavior.

**Architecture:** `workflow_v2.epub_output` owns input fingerprints, EPUB assembly/validation and strict generated-manifest validation. `workflow_v2.epub_cli` integrates with the existing `book.py build` command and adds read-only `build-status`; authoritative workflow state remains metadata/progress/review ledger and exact artifact bytes.

**Tech Stack:** Python 3.10+, stdlib only (`zipfile`, `xml.etree.ElementTree`, `html`, `hashlib`, `json`, `io`), existing Workflow v2 repository/storage/review APIs.

**Spec:** `docs/WORKFLOW_V2_EPUB_OUTPUT_DESIGN.md`

## Global Constraints

- Target branch: `refactor/workflow-engine-v2`; never modify `main`.
- Feature branch: `feature/workflow-v2-epub-output`.
- No third-party runtime dependency.
- Existing Markdown build remains default/backward-compatible.
- EPUB and `output/manifest.json` are generated projections, not `SchemaKind` authoritative documents.
- `private_external` final builds work without persisted source binary.
- Final build requires current PASS evidence; preview mode is explicit.
- Every behavior slice follows RED -> minimal GREEN -> full matrix checkpoint.

---

### Task 1: Build-input identity and generated-manifest status

**Files:**
- Create: `scripts/workflow_v2/epub_output.py`
- Test: `tests/test_workflow_v2_epub_output.py`

**Interfaces:**
- `EpubOutputError(RuntimeError)`
- `BUILD_CONTRACT = "epub-build-v1"`
- `OUTPUT_MANIFEST_PATH = "output/manifest.json"`
- `build_input_snapshot(metadata, progress, resolutions, artifact_reader, *, preview, cover_reader=None) -> dict[str, Any]`
- `input_fingerprint(snapshot) -> str`
- `validate_output_manifest(manifest) -> dict[str, Any]`
- `build_output_manifest(*, book_slug, preview, artifact_path, artifact_sha256, unit_count, input_fingerprint, repository_commit, state_revisions) -> dict[str, Any]`
- `resolve_output_status(manifest, *, artifact_bytes, current_fingerprint, expected_unit_count) -> dict[str, Any]`

- [ ] Write RED tests proving relevant translation/metadata/order/cover/current-review changes alter the fingerprint, while review record IDs/commits, raw ledger revision and repository commit do not.
- [ ] Write RED tests proving preview snapshots omit review identity.
- [ ] Write RED tests proving strict generated-manifest validation rejects unsafe paths and malformed hashes, and status distinguishes `missing/current/stale/invalid`.
- [ ] Run `python -m unittest discover -s tests -v`; accept RED only if failures are the missing Task 1 APIs and baseline remains green.
- [ ] Implement only the Task 1 APIs. Fingerprints use SHA-256 of compact, sorted canonical JSON. Final review identity includes only resolved state, exact hashes, workflow revision and review-contract revision.
- [ ] Run full suite GREEN and commit `feat: add EPUB build input identity and manifest status`.

---

### Task 2: Deterministic EPUB assembly and validator

**Files:**
- Modify: `scripts/workflow_v2/epub_output.py`
- Test: `tests/test_workflow_v2_epub_output.py`

**Interfaces:**
- `render_markdown_xhtml(title: str, markdown: str, *, language: str) -> bytes`
- `build_epub_bytes(*, book_slug, title, author, language, units, fingerprint, cover=None) -> bytes`
- `validate_epub_bytes(content: bytes, *, expected_unit_count: int) -> dict[str, Any]`

- [ ] Write RED deterministic-package tests: identical inputs -> identical bytes; first ZIP member is stored `mimetype`; OPF/nav/spine order matches units; metadata/language/CSS and optional cover are present.
- [ ] Write RED corruption tests for mimetype/container/OPF/nav/spine/missing or malformed/empty chapters/bad cover reference.
- [ ] Run full suite and verify failures are only missing Task 2 APIs.
- [ ] Implement stdlib writer/validator with fixed ZIP timestamps `(1980, 1, 1, 0, 0, 0)` and escaped Markdown subset (headings, paragraphs, lists, fenced code; raw HTML escaped).
- [ ] Run full suite GREEN and commit `feat: build and validate deterministic EPUB bytes`.

---

### Task 3: CLI build, persistence and build-status

**Files:**
- Create: `scripts/workflow_v2/epub_cli.py`
- Modify: `scripts/book.py`
- Test: `tests/test_workflow_v2_epub_cli.py`
- Regression: `tests/test_book_cli.py`

**Interfaces:**
- `epub_build_command(args, root: Path) -> int`
- `build_status_command(args, root: Path) -> int`
- `register_build_status_command(subparsers, root) -> None`

- [ ] Write RED end-to-end tests for `build --format epub`, `build-status --format epub --json`, Markdown default compatibility, final reviewed/PASS gate, explicit preview, private-source build, output extension safety and deterministic rerun.
- [ ] Run full suite and verify RED is only absent CLI behavior.
- [ ] Add `--format markdown|epub` (default `markdown`), EPUB dispatch, `build-status`, and expected `EpubCliError` handling without changing existing Markdown semantics.
- [ ] Reuse normalized structural/corpus preflight from status/finalize. Final builds require all current PASS; preview requires translated/reviewed non-empty units.
- [ ] Assemble+validate before writes. If current output already matches, do not rewrite. Otherwise write artifact via existing filesystem CAS/atomic replace, revalidate/hash, then write canonical generated manifest. Repository HEAD is nullable provenance only.
- [ ] Run full suite GREEN and commit `feat: add first-class EPUB build CLI and output status`.

---

### Task 4: #18 EPUB reliability and idempotence extension

**Files:**
- Create: `tests/test_workflow_v2_epub_reliability.py`

- [ ] Add scenarios: incomplete final build preserves prior output; simulated artifact-before-manifest crash is non-current and recoverable; translation/metadata/order/cover/current-review mutation is stale; semantically duplicate PASS remains current; unrelated Git commit remains current; identical successful rebuild preserves exact EPUB/manifest bytes and filesystem revision/inode where applicable.
- [ ] Run full suite. If a test exposes a real defect, retain RED evidence and fix only the owning implementation.
- [ ] Commit `test: cover EPUB build recovery and staleness`.

---

### Task 5: Final verification and integration audit

- [ ] Fresh exact-head GitHub matrix: Python 3.10 and 3.12 success; capture exact full-suite count from a job log.
- [ ] Requirement audit against #14: reviewed default, preview escape hatch, metadata/nav/spine/CSS/cover, validation, manifest provenance/hash, relevant staleness, private source, idempotence.
- [ ] PR audit: target integration; `behind_by=0`; merge-base matches integration base; only #14 docs/domain/CLI/tests changed; no unresolved comments/reviews/threads; PR mergeable; `main` unchanged.
- [ ] Only after clean guards, mark PR Ready and merge with expected head SHA into `refactor/workflow-engine-v2`. Preserve feature branch. Never merge to `main`.
