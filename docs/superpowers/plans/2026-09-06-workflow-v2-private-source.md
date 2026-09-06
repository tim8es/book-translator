# Workflow v2 Private Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every newly extracted Workflow v2 book start with a verified sealed corpus and explicit source identity, while allowing `private_external` sources to remain outside Git without blocking validation or resume.

**Architecture:** Extend the existing additive Workflow v2 metadata/source-manifest schemas rather than creating a new state file. Keep `scripts/corpus.py` as the single hash/integrity authority, make `scripts/book.py extract` create metadata + extracted files + manifest consistently, and have #10 status consume structured corpus verification output through the existing read-only preflight path.

**Tech Stack:** Python 3.10/3.12, stdlib `argparse`, `hashlib`, `json`, `pathlib`, `unittest`, existing Workflow v2 `FilesystemStorage` / `WorkflowStateRepository` / schemas.

**Spec:** `docs/WORKFLOW_V2_PRIVATE_SOURCE_DESIGN.md`

## Global Constraints

- New source storage mode is exactly `embedded` or `private_external`.
- Source identity is filename + format + byte size + exact lowercase SHA-256.
- New books are sealed at successful extraction time; no successful new explicit-source workspace remains `unsealed`.
- `private_external` must validate and verify without the original binary in the workspace when the sealed extracted corpus is complete.
- `embedded` requires the canonical source binary and verifies its size/hash.
- Missing, partial, malformed, or hash-mismatched explicit corpus blocks `resume`.
- Existing books without `metadata.source` retain legacy behavior; do not silently migrate them.
- Do not introduce `status.json`, a database, queue, external service, GitHub backend, finalization, or workflow migration behavior.
- `main` is never modified; all work stays on `feature/workflow-v2-private-source` until a later integration gate.

---

## File Structure

- `scripts/workflow_v2/schemas.py`: validate optional explicit `metadata.source` and additive source-manifest identity fields while preserving legacy documents.
- `scripts/book.py`: compute source identity during extract, support `--private-source`, create new books sealed, and make structural validation mode-aware.
- `scripts/corpus.py`: remain the single implementation of source/extracted hashing, structured manifest verification, exact restore identity checks, and private restore semantics.
- `scripts/workflow_v2/status_cli.py`: translate structured corpus verification into deterministic #10 status fields and fail closed for explicit-source books missing a manifest.
- `tests/test_workflow_v2_private_source.py`: primary #11 end-to-end contract tests for new-book extraction/schema/validation.
- `tests/test_corpus_cli.py`: extend existing corpus verify/restore regressions.
- `tests/test_workflow_v2_status_cli.py`: update new-book expectations from `unsealed` to verified and add explicit invalid/private reporting coverage.

### Task 1: Explicit source schema and sealed initialization

**Files:**
- Create: `tests/test_workflow_v2_private_source.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/book.py`
- Modify: `scripts/corpus.py`

**Interfaces:**
- Produces metadata source contract:
  `metadata["source"] -> {"storage_mode": str, "filename": str, "size_bytes": int, "sha256": str}`.
- Produces manifest fields:
  `source_storage_mode`, `source_size_bytes`, plus existing `source_file`, `source_format`, `source_sha256`, `chapter_count`, `extracted`.
- Produces helper in `corpus.py`:
  `build_manifest(book_dir: Path, metadata: dict, progress: dict, source: Path | None = None) -> dict` using explicit metadata identity when enabled and hashing the source only when required/available.

- [ ] **Step 1: Write failing end-to-end initialization tests**

Create `tests/test_workflow_v2_private_source.py` using the same temporary-repository copying pattern as `tests/test_corpus_cli.py`. Include these exact behavioral assertions:

```python
def test_new_embedded_book_records_identity_and_is_sealed(self):
    source = self.repo / "sample.md"
    source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    self.run_book("extract", str(source), "--slug", "sample", "--target-language", "ru")

    book = self.repo / "books" / "sample"
    metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))
    self.assertEqual(metadata["source"], {
        "storage_mode": "embedded",
        "filename": "sample.md",
        "size_bytes": len(source.read_bytes()),
        "sha256": expected,
    })
    self.assertEqual(manifest["source_storage_mode"], "embedded")
    self.assertEqual(manifest["source_size_bytes"], len(source.read_bytes()))
    self.assertEqual(manifest["source_sha256"], expected)
    self.assertTrue((book / "source" / "sample.md").is_file())
    self.run_corpus("verify", "sample")
```

```python
def test_new_private_book_is_sealed_without_persisting_source_binary(self):
    source = self.repo / "private.md"
    source.write_text("# One\n\nSecret source.\n", encoding="utf-8")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    self.run_book(
        "extract", str(source), "--slug", "private-book",
        "--target-language", "ru", "--private-source",
    )

    book = self.repo / "books" / "private-book"
    metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))
    self.assertEqual(metadata["source"]["storage_mode"], "private_external")
    self.assertEqual(metadata["source"]["sha256"], expected)
    self.assertEqual(manifest["source_storage_mode"], "private_external")
    self.assertFalse((book / "source" / "private.md").exists())
    self.run_book("validate", "private-book")
    self.run_corpus("verify", "private-book")
```

Also add schema-level mutation tests that change metadata source filename/hash/mode or manifest identity and assert validation/verification rejects the mismatch rather than normalizing it.

- [ ] **Step 2: Commit RED tests before production changes**

Commit only the new test file:

```bash
git add tests/test_workflow_v2_private_source.py
git commit -m "test: define #11 explicit source initialization"
```

Because the connected execution environment does not expose an arbitrary shell runner, use the PR CI matrix after opening the draft PR as the executable RED witness. Expected failures before implementation: `metadata["source"]` missing, `--private-source` unrecognized, `source-manifest.json` absent after plain extract.

- [ ] **Step 3: Implement additive schemas**

In `_validate_metadata`, when `source` is present:

```python
source = _require_mapping(data, "source", schema)
storage_mode = _require_nonempty_string(source, "storage_mode", schema, path="source.storage_mode")
if storage_mode not in {"embedded", "private_external"}:
    raise _field(schema, "source.storage_mode", "must be embedded or private_external")
filename = _require_nonempty_string(source, "filename", schema, path="source.filename")
_validate_basename(filename, schema, "source.filename")
if filename != data["source_file"]:
    raise _field(schema, "source.filename", "must equal source_file")
_require_int(source, "size_bytes", schema, minimum=0, path="source.size_bytes")
sha256 = _require_nonempty_string(source, "sha256", schema, path="source.sha256")
_validate_sha256(sha256, schema, "source.sha256")
```

In `_validate_source_manifest`, keep legacy fields valid and validate additive fields only when present. Require `source_storage_mode` to be one of the two modes and `source_size_bytes >= 0`; reject one being present without the other so a new explicit manifest cannot be half-declared.

- [ ] **Step 4: Implement source identity during extract**

In `extract_command`, before workspace writes derive:

```python
source_bytes = source.read_bytes()
source_identity = {
    "storage_mode": "private_external" if args.private_source else "embedded",
    "filename": source.name,
    "size_bytes": len(source_bytes),
    "sha256": hashlib.sha256(source_bytes).hexdigest(),
}
```

Add `--private-source` as a boolean flag to the existing `extract` parser. Add the `source` object to new metadata. Preserve existing top-level `source_file` and `source_format`.

For embedded mode, retain the existing source copy. For private mode, do not create/copy `source/<filename>`.

- [ ] **Step 5: Create and validate the manifest during extract**

Refactor `corpus.build_manifest` so explicit metadata can supply durable source identity while the function still hashes the provided source and confirms it matches metadata before constructing the manifest. Add `source_storage_mode` and `source_size_bytes` for explicit-source books.

Call the shared corpus manifest builder/writer from the new-book initialization flow after extracted files and metadata/progress exist. Do not duplicate extracted-file hashing in `book.py`.

A failure during manifest construction must use the existing extraction cleanup path so the command does not report success for a half-initialized book.

- [ ] **Step 6: Make structural validation mode-aware**

In `validate_book`:

```python
source_contract = metadata.get("source")
if isinstance(source_contract, dict):
    mode = source_contract.get("storage_mode")
    if mode == "embedded" and not canonical_source.is_file():
        errors.append(...)
    # private_external: canonical source absence is valid
    if not (book_dir / "source-manifest.json").is_file():
        errors.append("Missing source-manifest.json for explicit-source book")
else:
    # preserve current legacy source-file requirement exactly
```

Do not hash artifacts in `validate_book`.

- [ ] **Step 7: Run focused Task 1 tests and full suite**

Focused command when a shell runner is available:

```bash
python -m unittest tests.test_workflow_v2_private_source -v
```

Full command:

```bash
python -m unittest discover -s tests -v
```

On GitHub CI, require both Python 3.10 and 3.12 jobs green before moving to Task 2.

- [ ] **Step 8: Commit Task 1 GREEN**

```bash
git add scripts/book.py scripts/corpus.py scripts/workflow_v2/schemas.py tests/test_workflow_v2_private_source.py
git commit -m "feat: seal new books with explicit source identity"
```

### Task 2: Corpus verification and resume gate

**Files:**
- Modify: `scripts/corpus.py`
- Modify: `scripts/workflow_v2/status_cli.py`
- Modify: `tests/test_workflow_v2_private_source.py`
- Modify: `tests/test_workflow_v2_status_cli.py`

**Interfaces:**
- Replace tuple-only verifier result with a structured mapping returned by `verify_manifest(...)`:

```python
{
    "state": "verified",
    "storage_mode": "embedded" | "private_external" | "legacy_embedded",
    "source_file": str,
    "source_sha256": str,
    "source_size_bytes": int | None,
    "source_attached": bool,
    "chapter_count": int,
}
```

Internal CLI callers use mapping keys instead of unpacking `(source_sha, chapter_count)`.

- [ ] **Step 1: Write RED verification/resume tests**

Update the new-book expectation in `tests/test_workflow_v2_status_cli.py`: a freshly extracted book is now `corpus.state == "verified"`, not `unsealed`.

Add:

```python
def test_private_source_status_is_verified_without_binary(self):
    book = self.initialize_book(private=True)
    status = self.canonical_json(self.run_book("status", "sample", "--json"))
    self.assertTrue(status["valid"])
    self.assertEqual(status["corpus"]["state"], "verified")
    self.assertEqual(status["corpus"]["storage_mode"], "private_external")
    self.assertFalse(status["corpus"]["source_attached"])
```

```python
def test_explicit_source_missing_manifest_blocks_resume(self):
    book = self.initialize_book()
    (book / "source-manifest.json").unlink()
    result = self.run_book("resume", "sample", "--json", expect=1)
    payload = self.canonical_json(result)
    self.assertEqual(payload["operation"], "blocked")
    self.assertEqual(payload["reason"], "preflight_failed")
    self.assertTrue(any("source-manifest.json" in error for error in payload["errors"]))
```

Add private partial/tampered extracted corpus cases and one handcrafted legacy workspace case proving missing manifest remains `unsealed` for metadata without `source`.

- [ ] **Step 2: Commit Task 2 RED tests**

```bash
git add tests/test_workflow_v2_private_source.py tests/test_workflow_v2_status_cli.py
git commit -m "test: define #11 corpus verification gate"
```

- [ ] **Step 3: Extend `verify_manifest` without duplicating hashing**

For explicit metadata:

1. compare metadata `source` identity to manifest identity;
2. verify every progress chapter and extracted hash exactly as today;
3. for `embedded`, require canonical source, size-match it, then hash-match it;
4. for `private_external`, permit canonical source absence; if present, size/hash verify it;
5. return the structured verified mapping above.

For legacy metadata, preserve the existing canonical source requirement and return `storage_mode="legacy_embedded"`, `source_size_bytes=None`, `source_attached=True`.

- [ ] **Step 4: Make #10 preflight explicit-source fail closed**

In `_default_preflight`:

```python
explicit_source = isinstance(metadata.get("source"), Mapping)
if not manifest_path.is_file():
    if explicit_source:
        return structural_errors, {
            "state": "invalid",
            "storage_mode": metadata["source"].get("storage_mode"),
            "error": "source-manifest.json is missing for explicit-source book",
        }
    return structural_errors, {"state": "unsealed"}
```

When a manifest exists, return the structured mapping from `corpus.verify_manifest` directly. On error preserve mode when safely available and return `state="invalid"` plus the error.

- [ ] **Step 5: Update human status source summary**

Keep output concise. Replace only the corpus line for explicit verified state with a stable addition such as:

```text
claims=0 corpus=verified source=private_external attached=no
```

Legacy human output may remain `corpus=unsealed` or `corpus=verified source=legacy_embedded attached=yes`.

- [ ] **Step 6: Run focused and full tests, then commit GREEN**

```bash
python -m unittest tests.test_workflow_v2_private_source tests.test_workflow_v2_status_cli -v
python -m unittest discover -s tests -v
```

Require both CI matrix jobs green, then:

```bash
git add scripts/corpus.py scripts/workflow_v2/status_cli.py tests/test_workflow_v2_private_source.py tests/test_workflow_v2_status_cli.py
git commit -m "feat: block resume on invalid explicit source corpus"
```

### Task 3: Exact restore and temporary private reattachment

**Files:**
- Modify: `scripts/corpus.py`
- Modify: `tests/test_corpus_cli.py`

**Interfaces:**
- Add/centralize source identity check:

```python
def verify_supplied_source_identity(source: Path, metadata: dict, manifest: dict | None, expected_sha256: str | None) -> dict:
    """Return trusted explicit/legacy source identity or raise BookError before writes."""
```

The returned mapping contains `source_format`, `source_file`, `source_sha256`, `source_size_bytes`, and `storage_mode`.

- [ ] **Step 1: Write RED restore tests**

Add to `tests/test_corpus_cli.py`:

```python
def test_private_restore_rejects_same_name_different_hash_before_writes(self):
    # create private explicit book
    # snapshot extracted files + manifest bytes
    # replace external source bytes while retaining basename
    # restore must fail with identity mismatch
    # extracted files and manifest bytes must remain byte-for-byte unchanged
```

```python
def test_private_restore_rebuilds_without_persisting_binary(self):
    # create private explicit book
    # delete one extracted artifact
    # restore with the exact original external source
    # all extracted files return and corpus verifies
    # books/<slug>/source/<filename> remains absent
```

Also cover explicit size mismatch and preserve the existing legacy/embedded restore regression.

- [ ] **Step 2: Commit Task 3 RED tests**

```bash
git add tests/test_corpus_cli.py
git commit -m "test: define #11 exact private source restore"
```

- [ ] **Step 3: Validate exact source identity before any restore mutation**

Before extracting/staging:

- explicit source basename must equal metadata source filename;
- detected format must equal `source_format`;
- byte size must equal recorded `size_bytes`;
- SHA-256 must equal recorded `sha256`;
- manifest identity, when present, must agree with metadata;
- `--expected-sha256`, when present, must agree with durable identity.

Retain legacy behavior where manifest hash or explicit CLI hash supplies trusted identity.

- [ ] **Step 4: Make the restore commit path mode-aware**

For `embedded`, keep the staged-source copy and atomic replace behavior.

For `private_external`, do not stage/copy a canonical source file. Replace only the reconstructed `extracted/` directory and source manifest after all hash/title/count checks pass.

On any failure, prior extracted files, metadata/progress, and manifest must remain unchanged.

- [ ] **Step 5: Run focused/full tests and commit GREEN**

```bash
python -m unittest tests.test_corpus_cli -v
python -m unittest discover -s tests -v
```

Then:

```bash
git add scripts/corpus.py tests/test_corpus_cli.py
git commit -m "feat: restore private corpus without persisting source binary"
```

### Task 4: Status reporting, compatibility regression, and final verification

**Files:**
- Modify: `tests/test_workflow_v2_status_cli.py`
- Modify: `tests/test_workflow_v2_private_source.py`
- Modify: `scripts/workflow_v2/status_cli.py` only if a RED assertion requires presentation adjustment.
- Modify: `docs/WORKFLOW_V2_PRIVATE_SOURCE_DESIGN.md` only if implementation uncovered a contradiction; otherwise leave spec unchanged.

**Interfaces:**
- Stable explicit verified status corpus keys:
  `state`, `storage_mode`, `source_file`, `source_sha256`, `source_size_bytes`, `source_attached`, `chapter_count`.
- `resume` remains read-only and does not add files to its bounded context descriptor.

- [ ] **Step 1: Add final RED/coverage assertions**

Verify canonical JSON ordering through the existing `canonical_json` helper and assert:

```python
self.assertEqual(status["corpus"], {
    "chapter_count": 1,
    "source_attached": False,
    "source_file": "sample.md",
    "source_sha256": expected_sha,
    "source_size_bytes": expected_size,
    "state": "verified",
    "storage_mode": "private_external",
})
```

Assert status/resume remain byte-for-byte read-only over `metadata.json`, `progress.json`, `review-ledger.json`, and `source-manifest.json`.

Assert a handcrafted legacy book without `metadata.source` still validates with the old structural rules and may report `unsealed` when no manifest exists.

- [ ] **Step 2: Commit coverage tests before any presentation fix**

```bash
git add tests/test_workflow_v2_status_cli.py tests/test_workflow_v2_private_source.py
git commit -m "test: lock #11 source reproducibility status"
```

- [ ] **Step 3: Apply only minimal production changes required by RED**

Do not change `StatusResolver` operation selection or bounded context files. Source reproducibility remains a preflight-provided `corpus` mapping. If human output needs adjustment, change only formatting in `status_cli.py`.

- [ ] **Step 4: Run final focused suites**

```bash
python -m unittest tests.test_workflow_v2_private_source -v
python -m unittest tests.test_corpus_cli -v
python -m unittest tests.test_workflow_v2_status_cli -v
```

Expected: all #11 tests pass.

- [ ] **Step 5: Run full matrix suite**

```bash
python -m unittest discover -s tests -v
```

Require GitHub Actions `unit-tests (3.10)` and `unit-tests (3.12)` both `success`. Inspect logs for the final total test count and confirm every #11 regression is individually `ok`.

- [ ] **Step 6: Self-review diff against integration**

Confirm:

- branch merge base is still the intended integration lineage or synchronize before final CI;
- no `main` modification;
- no branch deletion;
- no new derived state file;
- no finalize/migration/GitHub-backend/parallel scheduling scope;
- private-source binary is absent from repository changes;
- only expected production/tests/docs files changed.

- [ ] **Step 7: Open PR to integration branch**

Open a PR from `feature/workflow-v2-private-source` to `refactor/workflow-engine-v2` with `Closes #11`, TDD RED/GREEN evidence, final matrix run IDs, compatibility notes, exact changed files, and explicit statement that `main` is untouched.

Keep it draft until all final matrix jobs pass; then mark Ready for review. Do not merge without a separate integration authorization.
