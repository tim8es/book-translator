# Workflow v2 Private Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every newly extracted Workflow v2 book start with a verified sealed corpus and explicit source identity, while allowing `private_external` sources to remain outside Git without blocking validation or resume.

**Architecture:** Extend the existing additive Workflow v2 metadata/source-manifest schemas rather than creating a new state file. Keep `scripts/corpus.py` as the single hash/integrity authority, make `scripts/book.py extract` create metadata + extracted files + manifest consistently, and have #10 status consume structured corpus verification output through the existing read-only preflight path.

**Tech Stack:** Python 3.10/3.12, stdlib `argparse`, `hashlib`, `json`, `pathlib`, `unittest`, existing Workflow v2 `FilesystemStorage` / `WorkflowStateRepository` / schemas.

**Spec:** `docs/WORKFLOW_V2_PRIVATE_SOURCE_DESIGN.md`

## Global Constraints

- Source storage mode is exactly `embedded` or `private_external` for new explicit-source books.
- Source identity is filename + format + byte size + exact lowercase SHA-256.
- New books are sealed at successful extraction time; no successful new explicit-source workspace remains `unsealed`.
- `private_external` verifies without the original binary when the sealed extracted corpus is complete.
- `embedded` requires the canonical source binary and verifies its size/hash.
- Missing, partial, malformed, or hash-mismatched explicit corpus blocks `resume`.
- Existing books without `metadata.source` retain legacy behavior; no silent migration.
- No `status.json`, database, queue, external service, GitHub backend, finalization, or migration behavior is introduced.
- `main` is never modified.

---

## File Structure

- `scripts/workflow_v2/schemas.py`: optional explicit `metadata.source` and additive manifest identity validation.
- `scripts/book.py`: source identity during extract, `--private-source`, automatic seal, mode-aware structural validation.
- `scripts/corpus.py`: single source/extracted hash authority, structured verification, exact restore, private reattachment semantics.
- `scripts/workflow_v2/status_cli.py`: explicit-source fail-closed preflight and deterministic source reproducibility reporting.
- `tests/test_workflow_v2_private_source.py`: new-book explicit-source contracts.
- `tests/test_corpus_cli.py`: verify/restore regressions.
- `tests/test_workflow_v2_status_cli.py`: status/resume integration and compatibility.

### Task 1: Explicit source schema and sealed initialization

**Files:**
- Create: `tests/test_workflow_v2_private_source.py`
- Modify: `scripts/workflow_v2/schemas.py`
- Modify: `scripts/book.py`
- Modify: `scripts/corpus.py`

**Interfaces:**
- Metadata: `source = {storage_mode, filename, size_bytes, sha256}`.
- Manifest adds `source_storage_mode` and `source_size_bytes` for explicit-source books.
- `corpus.build_manifest(book_dir, metadata, progress, source)` remains the shared manifest builder.

- [ ] **Step 1: Write RED initialization tests**

Use the temporary-repository pattern from `tests/test_corpus_cli.py` and add:

```python
def test_new_embedded_book_records_identity_and_is_sealed(self):
    source = self.repo / "sample.md"
    source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    expected_sha = hashlib.sha256(source_bytes).hexdigest()

    self.run_book("extract", str(source), "--slug", "sample", "--target-language", "ru")
    book = self.repo / "books" / "sample"
    metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))

    self.assertEqual(metadata["source"], {
        "storage_mode": "embedded",
        "filename": "sample.md",
        "size_bytes": len(source_bytes),
        "sha256": expected_sha,
    })
    self.assertEqual(manifest["source_storage_mode"], "embedded")
    self.assertEqual(manifest["source_size_bytes"], len(source_bytes))
    self.assertEqual(manifest["source_sha256"], expected_sha)
    self.assertTrue((book / "source" / "sample.md").is_file())
    self.run_corpus("verify", "sample")
```

```python
def test_new_private_book_is_sealed_without_source_binary(self):
    source = self.repo / "private.md"
    source.write_text("# One\n\nSecret.\n", encoding="utf-8")
    source_bytes = source.read_bytes()
    expected_sha = hashlib.sha256(source_bytes).hexdigest()

    self.run_book(
        "extract", str(source), "--slug", "private-book",
        "--target-language", "ru", "--private-source",
    )
    book = self.repo / "books" / "private-book"
    metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
    manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))

    self.assertEqual(metadata["source"]["storage_mode"], "private_external")
    self.assertEqual(metadata["source"]["filename"], "private.md")
    self.assertEqual(metadata["source"]["size_bytes"], len(source_bytes))
    self.assertEqual(metadata["source"]["sha256"], expected_sha)
    self.assertEqual(manifest["source_storage_mode"], "private_external")
    self.assertFalse((book / "source" / "private.md").exists())
    self.run_book("validate", "private-book")
    self.run_corpus("verify", "private-book")
```

Add mutation cases that set `metadata["source"]["filename"] = "other.md"`, `storage_mode = "unknown"`, or replace one manifest identity field and assert validation/verification exits 1.

- [ ] **Step 2: Commit RED tests only**

```bash
git add tests/test_workflow_v2_private_source.py
git commit -m "test: define #11 explicit source initialization"
```

Expected RED causes: `metadata.source` absent, `--private-source` not recognized, automatic manifest absent.

- [ ] **Step 3: Extend schema validation**

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

In `_validate_source_manifest`, preserve legacy manifests. If either `source_storage_mode` or `source_size_bytes` exists, require both; validate mode and non-negative size.

- [ ] **Step 4: Add explicit source identity to extract**

Add `import hashlib`. Before workspace mutation:

```python
source_bytes = source.read_bytes()
source_identity = {
    "storage_mode": "private_external" if args.private_source else "embedded",
    "filename": source.name,
    "size_bytes": len(source_bytes),
    "sha256": hashlib.sha256(source_bytes).hexdigest(),
}
```

Add `--private-source` to the existing extract parser. Store `source_identity` at `metadata["source"]`. For `private_external`, skip the canonical source copy; for embedded preserve it.

- [ ] **Step 5: Seal during new-book initialization**

Extend `corpus.build_manifest` so explicit metadata identity is copied into manifest and the supplied source bytes are checked against metadata identity. Reuse its existing extracted-file hashing. Call the shared builder/writer from `book.py extract`; do not duplicate extracted hashes in `book.py`.

- [ ] **Step 6: Make structural validation mode-aware**

Use exact messages:

```python
source_contract = metadata.get("source")
canonical_source = book_dir / "source" / str(metadata.get("source_file"))
if isinstance(source_contract, dict):
    if source_contract.get("storage_mode") == "embedded" and not canonical_source.is_file():
        errors.append(f"Embedded source file does not exist: source/{metadata.get('source_file')}")
    if not (book_dir / "source-manifest.json").is_file():
        errors.append("Missing source-manifest.json for explicit-source book")
else:
    if not canonical_source.is_file():
        errors.append(f"Source file declared in metadata.json does not exist: source/{metadata.get('source_file')}")
```

Do not hash in `validate_book`.

- [ ] **Step 7: Verify GREEN and commit**

```bash
python -m unittest tests.test_workflow_v2_private_source -v
python -m unittest discover -s tests -v
```

Require Python 3.10/3.12 CI success, then:

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
- `verify_manifest(...) -> dict` with keys `state`, `storage_mode`, `source_file`, `source_sha256`, `source_size_bytes`, `source_attached`, `chapter_count`.

- [ ] **Step 1: Write RED verification/status tests**

Update fresh new-book status expectation to `verified`. Add:

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

For private mode assert:

```python
self.assertEqual(status["corpus"]["storage_mode"], "private_external")
self.assertFalse(status["corpus"]["source_attached"])
```

Tamper/delete one extracted artifact and assert `resume --json` exits 1 with `preflight_failed`. Create a legacy fixture by removing `metadata.source` and `source-manifest.json` while retaining the embedded source; assert `status["corpus"]["state"] == "unsealed"`.

- [ ] **Step 2: Commit Task 2 RED tests**

```bash
git add tests/test_workflow_v2_private_source.py tests/test_workflow_v2_status_cli.py
git commit -m "test: define #11 corpus verification gate"
```

- [ ] **Step 3: Return structured verification**

For explicit metadata, require exact metadata↔manifest identity agreement. Always verify chapter count/order/path/title/hash. For embedded, require canonical source and exact size/hash. For private, allow canonical source absence but verify it if present.

Return:

```python
return {
    "state": "verified",
    "storage_mode": storage_mode,
    "source_file": metadata["source_file"],
    "source_sha256": expected_source_sha,
    "source_size_bytes": expected_size,
    "source_attached": source.is_file(),
    "chapter_count": len(items),
}
```

For legacy metadata keep the current source-file requirement and return `storage_mode="legacy_embedded"`, `source_size_bytes=None`, `source_attached=True`.

- [ ] **Step 4: Make #10 preflight fail closed for explicit source**

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

When verification succeeds, return its structured mapping directly. On verification failure return `state="invalid"`, preserve storage mode when available, and include `error`.

- [ ] **Step 5: Keep human status concise**

For explicit verified corpus produce:

```text
claims=0 corpus=verified source=private_external attached=no
```

Use `yes/no` for attachment. Legacy `unsealed` output remains valid.

- [ ] **Step 6: Verify GREEN and commit**

```bash
python -m unittest tests.test_workflow_v2_private_source tests.test_workflow_v2_status_cli -v
python -m unittest discover -s tests -v
```

Then:

```bash
git add scripts/corpus.py scripts/workflow_v2/status_cli.py tests/test_workflow_v2_private_source.py tests/test_workflow_v2_status_cli.py
git commit -m "feat: block resume on invalid explicit source corpus"
```

### Task 3: Exact restore and temporary private reattachment

**Files:**
- Modify: `scripts/corpus.py`
- Modify: `tests/test_corpus_cli.py`

**Interfaces:**
- Add `verify_supplied_source_identity(source, metadata, manifest, expected_sha256) -> dict` and call it before any restore mutation.

- [ ] **Step 1: Write RED private restore tests**

Exact-source success test:

```python
def test_private_restore_rebuilds_without_persisting_binary(self):
    source = self.repo / "sample.epub"
    self.make_epub(source)
    self.run_cli(
        "book.py", "extract", str(source), "--slug", "sample",
        "--target-language", "ru", "--private-source",
    )
    book = self.repo / "books" / "sample"
    progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
    missing = book / progress["chapters"][0]["source_path"]
    missing.unlink()

    self.run_cli("corpus.py", "restore", "sample", str(source))
    self.assertTrue(missing.is_file())
    self.assertFalse((book / "source" / "sample.epub").exists())
    self.run_cli("corpus.py", "verify", "sample")
```

Same-name/different-hash rejection test:

```python
def test_private_restore_rejects_same_name_different_hash_before_writes(self):
    source_dir = self.repo / "original"
    source_dir.mkdir()
    source = source_dir / "sample.epub"
    self.make_epub(source)
    self.run_cli(
        "book.py", "extract", str(source), "--slug", "sample",
        "--target-language", "ru", "--private-source",
    )
    book = self.repo / "books" / "sample"
    before_manifest = (book / "source-manifest.json").read_bytes()
    before_extracted = {
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in (book / "extracted").glob("*.md")
    }

    replacement_dir = self.repo / "replacement"
    replacement_dir.mkdir()
    replacement = replacement_dir / "sample.epub"
    self.make_epub(replacement, body_suffix=" changed")
    result = self.run_cli("corpus.py", "restore", "sample", str(replacement), expect=1)

    self.assertIn("SHA-256 mismatch", result.stderr)
    self.assertEqual((book / "source-manifest.json").read_bytes(), before_manifest)
    self.assertEqual({
        path.relative_to(book).as_posix(): path.read_bytes()
        for path in (book / "extracted").glob("*.md")
    }, before_extracted)
```

Add a wrong-size explicit source case by appending bytes to a same-name copy and assert error contains `size mismatch` before state changes.

- [ ] **Step 2: Commit Task 3 RED tests**

```bash
git add tests/test_corpus_cli.py
git commit -m "test: define #11 exact private source restore"
```

- [ ] **Step 3: Implement pre-write identity validation**

For explicit source require basename, detected format, byte size, and SHA-256 to match metadata. Require manifest identity to agree with metadata. Require any `--expected-sha256` to agree with durable identity.

For legacy source retain current trusted-hash behavior from manifest or `--expected-sha256`.

- [ ] **Step 4: Make restore commit path mode-aware**

For embedded retain staged-source copy/replacement. For private do not stage or write a canonical source; atomically replace only reconstructed `extracted/` and the manifest after all checks pass. Preserve rollback behavior on write failure.

- [ ] **Step 5: Verify GREEN and commit**

```bash
python -m unittest tests.test_corpus_cli -v
python -m unittest discover -s tests -v
```

Then:

```bash
git add scripts/corpus.py tests/test_corpus_cli.py
git commit -m "feat: restore private corpus without persisting source binary"
```

### Task 4: Final status coverage and verification

**Files:**
- Modify: `tests/test_workflow_v2_status_cli.py`
- Modify: `tests/test_workflow_v2_private_source.py`
- Modify: `scripts/workflow_v2/status_cli.py` only if the RED presentation assertions require it.

**Interfaces:**
- Explicit verified status corpus keys are exactly `state`, `storage_mode`, `source_file`, `source_sha256`, `source_size_bytes`, `source_attached`, `chapter_count`.
- `resume` remains read-only and bounded-context files do not change.

- [ ] **Step 1: Add final RED/coverage assertions**

For a private one-chapter book assert:

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

Hash `metadata.json`, `progress.json`, `review-ledger.json`, and `source-manifest.json` before and after `status` + `resume`; assert byte hashes unchanged and `status.json` absent.

- [ ] **Step 2: Commit coverage tests**

```bash
git add tests/test_workflow_v2_status_cli.py tests/test_workflow_v2_private_source.py
git commit -m "test: lock #11 source reproducibility status"
```

- [ ] **Step 3: Apply only production changes required by RED**

Do not change `StatusResolver` operation selection or context descriptors. If necessary, adjust only source summary formatting in `status_cli.py`.

- [ ] **Step 4: Run focused suites**

```bash
python -m unittest tests.test_workflow_v2_private_source -v
python -m unittest tests.test_corpus_cli -v
python -m unittest tests.test_workflow_v2_status_cli -v
```

- [ ] **Step 5: Run full matrix suite**

```bash
python -m unittest discover -s tests -v
```

Require GitHub Actions `unit-tests (3.10)` and `unit-tests (3.12)` both `success`. Inspect final logs for total count and individual #11 tests.

- [ ] **Step 6: Self-review branch diff**

Verify exact integration ancestry, `main` untouched, no branch deletions, no private binary in changes, no derived status state, and no #12/#16/#17/#15 scope.

- [ ] **Step 7: Open PR to integration**

Open a PR `feature/workflow-v2-private-source` → `refactor/workflow-engine-v2` with `Closes #11`, RED/GREEN run evidence, final matrix run IDs, compatibility notes, changed-file list, and explicit `main` untouched statement. Keep Draft until final matrix is green, then mark Ready for review. Do not merge without a separate integration authorization.
