# Workflow v2 EPUB Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic first-class EPUB build/validation, output manifest and stale-output detection while preserving Markdown build behavior.

**Architecture:** A new stdlib-only `workflow_v2.epub_output` domain module owns input fingerprints, EPUB assembly/validation and manifest status. A thin `workflow_v2.epub_cli` adapter integrates with the existing `book.py build` command and adds read-only `build-status`; authoritative workflow state remains metadata/progress/review ledger and exact artifacts.

**Tech Stack:** Python 3.10+, stdlib `zipfile`, `xml.etree.ElementTree`, `html`, `hashlib`, `json`, `io`, existing Workflow v2 repository/storage/review APIs.

**Spec:** `docs/WORKFLOW_V2_EPUB_OUTPUT_DESIGN.md`

## Global Constraints

- Target branch is `refactor/workflow-engine-v2`; never modify `main`.
- Branch is `feature/workflow-v2-epub-output`.
- No third-party runtime dependency.
- Existing Markdown build remains default/backward-compatible.
- Generated EPUB/manifest are projections, not authoritative state.
- `private_external` builds must work without source binary persistence.
- Final build requires current PASS evidence; preview mode is explicit.
- Every task follows RED -> minimal GREEN -> full matrix checkpoint.

---

### Task 1: Build-input identity and output-manifest status

**Files:**
- Create: `scripts/workflow_v2/epub_output.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Test: `tests/test_workflow_v2_epub_output.py`

**Interfaces:**
- `EpubOutputError(RuntimeError)`
- `BUILD_CONTRACT = "epub-build-v1"`
- `OUTPUT_MANIFEST_PATH = "output/manifest.json"`
- `build_input_snapshot(metadata, progress, resolutions, artifact_reader, *, preview, cover_reader=None) -> dict[str, Any]`
- `input_fingerprint(snapshot) -> str`
- `build_output_manifest(*, book_slug, preview, artifact_path, artifact_sha256, unit_count, input_fingerprint, repository_commit, state_revisions) -> dict[str, Any]`
- `resolve_output_status(manifest, *, artifact_bytes, current_fingerprint, expected_unit_count) -> dict[str, Any]`
- Add `SchemaKind.OUTPUT_MANIFEST` with strict v1 validation for the manifest contract.

- [ ] **Step 1: Write RED tests for input identity.**

Tests must prove:

```python
snapshot_a = build_input_snapshot(metadata, progress, pass_resolutions, read_artifact, preview=False)
snapshot_b = build_input_snapshot(metadata, progress, duplicate_pass_resolutions, read_artifact, preview=False)
self.assertEqual(input_fingerprint(snapshot_a), input_fingerprint(snapshot_b))

translation_bytes["translated/001-a.md"] = b"changed"
self.assertNotEqual(input_fingerprint(snapshot_a), input_fingerprint(build_input_snapshot(...)))
```

Also assert metadata title/language/order/cover/review-state changes alter the fingerprint, while raw ledger revision and repository commit are not inputs.

- [ ] **Step 2: Write RED tests for manifest validation/status.**

Assert `current`, `stale`, `invalid` and `missing` boundaries using an exact artifact SHA and input fingerprint. Unsafe artifact paths and malformed SHA/fingerprint must fail schema/domain validation.

- [ ] **Step 3: Run full suite for RED witness.**

Run: `python -m unittest discover -s tests -v`

Expected: only the new Task 1 tests fail because `epub_output`/`OUTPUT_MANIFEST` are missing.

- [ ] **Step 4: Implement minimal Task 1 domain/schema.**

Canonical fingerprint serialization:

```python
def input_fingerprint(snapshot):
    content = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(content).hexdigest()
```

Final review identity must use normalized resolution fields only (`state`, source/translation hashes, workflow/review-contract revision), never raw record IDs or ledger revision.

- [ ] **Step 5: Run full suite GREEN and commit.**

Commit boundary: `feat: add EPUB build input identity and manifest status`.

---

### Task 2: Deterministic EPUB assembly and validator

**Files:**
- Modify: `scripts/workflow_v2/epub_output.py`
- Test: `tests/test_workflow_v2_epub_output.py`

**Interfaces:**
- `render_markdown_xhtml(title: str, markdown: str, *, language: str) -> bytes`
- `build_epub_bytes(*, book_slug, title, author, language, units, fingerprint, cover=None) -> bytes`
- `validate_epub_bytes(content: bytes, *, expected_unit_count: int) -> dict[str, Any]`

`units` is ordered data containing `number`, `title`, `slug` and exact Markdown text. `cover` is `None` or `{name, media_type, content}`.

- [ ] **Step 1: Write RED deterministic-package tests.**

Assert two calls with identical inputs produce identical bytes and ZIP invariants:

```python
first = build_epub_bytes(...)
second = build_epub_bytes(...)
self.assertEqual(first, second)
with zipfile.ZipFile(io.BytesIO(first)) as zf:
    self.assertEqual(zf.namelist()[0], "mimetype")
    self.assertEqual(zf.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
```

Assert OPF/nav/spine order matches input units, metadata target language is emitted and cover is optional.

- [ ] **Step 2: Write RED validator corruption tests.**

Corrupt/remove each critical contract independently: mimetype, container, OPF, nav, spine count, missing chapter, malformed XHTML, empty chapter, bad cover reference. Each must raise `EpubOutputError`.

- [ ] **Step 3: Run full suite for RED witness.**

Expected: only assembly/validator tests fail on missing APIs.

- [ ] **Step 4: Implement stdlib EPUB writer/validator.**

Use fixed ZIP timestamps `(1980, 1, 1, 0, 0, 0)` and explicit `ZipInfo` objects. Escape translation text; support headings, paragraphs, ordered/unordered lists and fenced code only. Do not execute raw HTML.

- [ ] **Step 5: Run full suite GREEN and commit.**

Commit boundary: `feat: build and validate deterministic EPUB bytes`.

---

### Task 3: CLI build, manifest persistence and build-status

**Files:**
- Create: `scripts/workflow_v2/epub_cli.py`
- Modify: `scripts/book.py`
- Test: `tests/test_workflow_v2_epub_cli.py`
- Test regression: `tests/test_book_cli.py`

**Interfaces:**
- `epub_build_command(args, root: Path) -> int`
- `build_status_command(args, root: Path) -> int`
- `register_build_status_command(subparsers, root) -> None`

- [ ] **Step 1: Write RED end-to-end CLI tests.**

Create a fully reviewed/PASS book using existing claim/review/finalize commands, then assert:

```text
book.py build sample --format epub
book.py build-status sample --format epub --json
```

produces a validated `output/sample.epub`, canonical `output/manifest.json`, and JSON state `current`.

Also cover:
- default `build sample` still creates Markdown;
- final EPUB rejects translated/unreviewed state;
- `--allow-unreviewed` creates `preview: true` manifest;
- `private_external` final build succeeds with no source binary;
- `.epub`/`.md` output extension mismatch fails without writes.

- [ ] **Step 2: Run full suite for RED witness.**

Expected: new CLI tests fail because `--format epub`/`build-status` are not registered.

- [ ] **Step 3: Implement thin CLI adapter and `book.py` wiring.**

`book.py` changes:

```python
build.add_argument("--format", choices=("markdown", "epub"), default="markdown")
```

At the start of `build_command`, dispatch EPUB to `epub_build_command`; preserve the existing Markdown path byte-for-byte except for output-extension validation where needed. Register `build-status` once and add `EpubCliError` to the expected CLI error boundary.

EPUB preflight reuses normalized structural/corpus preflight from `status_cli.default_preflight`. Final builds additionally resolve all review evidence and require current PASS for every unit; preview builds require translated/reviewed lifecycle and non-empty translations.

- [ ] **Step 4: Persist artifact/manifest safely.**

Candidate bytes must be assembled and validated before any final write. If existing manifest+artifact are already `current`, return unchanged without rewriting either. Otherwise use existing filesystem CAS/atomic replace operations; write manifest only after final artifact hash/validation succeeds.

Best-effort repository commit:

```python
subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, ...)
```

Failure/no Git repository -> `None`.

- [ ] **Step 5: Run full suite GREEN and commit.**

Commit boundary: `feat: add first-class EPUB build CLI and output status`.

---

### Task 4: #18 build reliability and idempotence extension

**Files:**
- Create: `tests/test_workflow_v2_epub_reliability.py`

- [ ] **Step 1: Add failure-boundary tests.**

Cover:

1. incomplete final build fails and leaves prior artifact/manifest unchanged;
2. artifact replaced but old manifest retained (simulated process death) resolves non-current and clean rerun repairs it;
3. translation/metadata/order/cover/current-review changes resolve `stale`;
4. semantically duplicate PASS evidence with unchanged current identity remains `current`;
5. unrelated Git commit remains `current`;
6. identical successful rebuild keeps exact EPUB bytes, manifest bytes and storage revisions/inodes where filesystem semantics allow.

- [ ] **Step 2: Run full suite.**

If a new test reveals a real defect, retain the failing witness, fix only the owning implementation, and rerun the complete matrix.

- [ ] **Step 3: Commit reliability coverage.**

Commit boundary: `test: cover EPUB build recovery and staleness`.

---

### Task 5: Final verification and integration audit

**Files:** no planned production changes.

- [ ] **Step 1: Fresh exact-head Python matrix.**

Require Python 3.10 and 3.12 GitHub CI success and capture exact test count from one full job log.

- [ ] **Step 2: Requirement audit.**

Verify every #14 acceptance item against tests/code: reviewed default, preview escape hatch, metadata/nav/spine/CSS/cover, validation, manifest provenance/hash, stale relevant inputs, private source, idempotence.

- [ ] **Step 3: PR audit.**

Require:
- base exactly current `refactor/workflow-engine-v2`;
- `behind_by=0` and merge-base equals integration head used for the branch;
- only #14 design/plan/domain/CLI/tests changed;
- no unresolved comments/reviews/threads;
- PR mergeable;
- `main` SHA unchanged.

- [ ] **Step 4: Ready and guarded merge.**

Only after all guards are clean, mark PR Ready and merge with expected head SHA into `refactor/workflow-engine-v2`. Preserve feature branch. Do not merge to `main`.
