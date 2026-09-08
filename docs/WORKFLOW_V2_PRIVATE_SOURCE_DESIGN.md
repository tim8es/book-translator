# Workflow v2 — Source corpus integrity and explicit private-source mode

Issue: #11
Branch: `feature/workflow-v2-private-source`
Base: `refactor/workflow-engine-v2` at `196c3ae12a0f981f69bd5a0adb67fd41d6c09686`
Final PR target: `refactor/workflow-engine-v2`
Date: 2026-09-06

## Purpose

Make source reproducibility machine-verifiable for every new Workflow v2 book without requiring copyrighted or private source binaries to be committed to Git.

Repository state remains authoritative. The durable identity of the original source is recorded separately from the question of whether the original binary is stored in the workspace. A complete sealed extracted corpus is sufficient for literary work only when its manifest verifies exactly against current extracted artifacts and the declared source identity.

## Scope

In scope:

- explicit source storage mode for new books: `embedded` or `private_external`;
- durable source filename, format, byte size, and SHA-256 identity;
- automatic sealed-corpus creation for new books after extraction;
- complete extracted-corpus verification before `resume` permits literary work;
- private-source validation and verification without a committed original binary;
- exact-source reattachment through the existing corpus restore flow;
- rejection of same-name/different-hash replacement sources;
- source reproducibility fields in Workflow v2 status output;
- backward compatibility for books created before the explicit source contract.

Out of scope:

- finalization behavior beyond exposing source reproducibility state to future #12;
- generated review reports (#21);
- GitHub storage/backend work (#17);
- workflow migration tooling (#16);
- parallel orchestration (#15);
- databases, queues, or mandatory external services;
- storing copyrighted/private source binaries in Git.

## Core invariants

1. Source identity is filename + format + byte size + exact SHA-256, not filename alone.
2. A new book is not ready for literary work until its extracted corpus is sealed and verifies completely.
3. `private_external` never requires the original binary to remain in the repository workspace after successful extraction/sealing.
4. A private source supplied later must match the recorded identity before any restore write occurs.
5. Same-name/different-hash source replacement is rejected.
6. Every extracted artifact listed by progress must have exactly one manifest entry with matching chapter identity, path, and exact SHA-256.
7. Partial, missing, malformed, or hash-mismatched extracted corpus blocks `resume`.
8. Source reproducibility is computed from authoritative metadata, manifest, and current files; no derived `status.json` is introduced.
9. Existing legacy books are not silently migrated to the new source contract.
10. The original source binary and the extracted corpus are different reproducibility layers: an embedded binary is additionally verified, while a private external binary may be absent after sealing.

## Durable source contract

New books created by this workflow include an explicit `source` object in `metadata.json`:

```json
{
  "source_file": "book.epub",
  "source_format": "epub",
  "source": {
    "storage_mode": "embedded",
    "filename": "book.epub",
    "size_bytes": 123456,
    "sha256": "<64 lowercase hex>"
  }
}
```

Rules:

- `storage_mode` is exactly `embedded` or `private_external`;
- `filename` is a safe basename and must equal legacy-compatible `source_file`;
- `size_bytes` is a non-negative integer;
- `sha256` is lowercase SHA-256;
- `source_format` remains the existing top-level compatibility field;
- new-book creation always writes the explicit object;
- legacy metadata without `source` retains legacy behavior until explicit migration work in #16.

The explicit `source` object is the enablement marker. No additional mutable mode file is introduced.

## Source manifest contract

`source-manifest.json` remains the authoritative sealed-corpus manifest and is extended for new explicit-source books:

```json
{
  "schema_version": 1,
  "source_file": "book.epub",
  "source_format": "epub",
  "source_storage_mode": "private_external",
  "source_size_bytes": 123456,
  "source_sha256": "<64 lowercase hex>",
  "chapter_count": 2,
  "extracted": [
    {
      "number": 1,
      "title": "Chapter One",
      "path": "extracted/001-chapter-one.md",
      "sha256": "<64 lowercase hex>"
    }
  ]
}
```

For new explicit-source books, metadata and manifest source identity must agree exactly on filename, format, storage mode, size, and hash.

Existing legacy manifests that predate `source_storage_mode` and `source_size_bytes` remain parseable for legacy books. The stricter fields are required when metadata contains the explicit `source` object.

## New-book extraction modes

### Embedded

Default extraction remains embedded:

```bash
python scripts/book.py extract <source> ...
```

The flow:

1. validate and read the input source;
2. compute source filename, format, byte size, and SHA-256;
3. extract all chapters;
4. write metadata/progress/support files/review ledger;
5. copy the original source to `books/<slug>/source/<filename>`;
6. construct `source-manifest.json` from the exact source identity and all extracted artifact hashes;
7. validate the manifest before completing initialization.

A successfully created new embedded book therefore starts sealed, not `unsealed`.

### Private external

Explicit private mode:

```bash
python scripts/book.py extract <source> ... --private-source
```

The source is available during extraction but is not retained in the book workspace.

The flow is identical through extraction and hashing, except no final copy of the original binary is placed under `books/<slug>/source/`. Metadata and manifest retain only its filename, format, size, and SHA-256 identity.

The extracted corpus and machine state remain suitable for Git storage. The private binary remains outside repository authority.

## Atomic initialization boundary

New-book initialization must not leave a valid-looking explicit-source workspace without a complete manifest.

Implementation should build and validate metadata, progress, extracted artifacts, review ledger, and source manifest as one initialization flow. If construction fails before completion, the command fails rather than reporting a successfully initialized unsealed Workflow v2 book.

This issue does not introduce a general transactional filesystem framework. It only preserves the existing extraction cleanup/error boundary while adding manifest creation to the required initialization set.

## Verification semantics

`corpus.verify_manifest()` remains the single corpus hash verifier reused by CLI/status. It is extended rather than duplicated.

Common verification for both storage modes:

1. parse metadata/progress/manifest;
2. require manifest source identity to match explicit metadata when present;
3. require manifest chapter count to match progress;
4. require one ordered manifest entry per progress chapter;
5. require number/title/path equality for each entry;
6. require every extracted artifact to exist;
7. hash every extracted artifact and require an exact match.

Additional `embedded` verification:

- `source/<filename>` must exist;
- byte size must match metadata/manifest;
- SHA-256 must match metadata/manifest.

Additional `private_external` verification:

- absence of `source/<filename>` is valid;
- if a source file is present at that canonical path, it must match recorded byte size and SHA-256 or verification fails;
- presence of a mismatched file is never ignored.

The verifier returns structured source/corpus identity sufficient for status instead of requiring callers to reconstruct mode semantics.

## Structural validation

For explicit-source books, `book.py validate` requires:

- the explicit metadata `source` object passes schema validation;
- `source_file`, `source_format`, and explicit source identity are internally consistent;
- `source-manifest.json` exists and passes schema validation;
- manifest source identity agrees with metadata;
- progress and manifest chapter counts/paths are structurally consistent;
- `embedded` requires the canonical source file to exist;
- `private_external` does not require the original source file to exist.

Structural validation is not a second hashing implementation. Exact source/extracted hashes remain the responsibility of `corpus.verify_manifest()` and the status/resume preflight that calls it.

Legacy books without the explicit `source` object retain current validation semantics and are not required to acquire a manifest automatically.

## Status and resume integration

For explicit-source books, absence of `source-manifest.json` is no longer reported as benign `unsealed`; it is `invalid` and blocks resume.

For legacy books, the existing `unsealed` compatibility result remains available.

Verified status exposes a normalized corpus/source summary:

```json
{
  "corpus": {
    "state": "verified",
    "storage_mode": "private_external",
    "source_file": "book.epub",
    "source_sha256": "...",
    "source_size_bytes": 123456,
    "source_attached": false,
    "chapter_count": 42
  }
}
```

`resume` remains read-only. It neither attaches sources nor seals/restores anything. If explicit-source verification fails, the existing #10 preflight path returns `blocked` before translator/reviewer work.

No derived status document is written.

## Restore and temporary reattachment

The existing command remains the reattachment surface:

```bash
python scripts/corpus.py restore <book> <source-path> [--expected-sha256 ...]
```

Before writes, restore verifies:

- source format matches recorded format;
- supplied source basename matches recorded filename for explicit-source books;
- supplied byte size matches recorded size;
- supplied SHA-256 matches recorded SHA-256;
- any explicit `--expected-sha256` agrees with durable identity;
- re-extracted chapter count/titles and, when already sealed, artifact hashes agree with durable corpus identity.

For `embedded`, successful restore may repopulate/update the canonical stored source as today, but only after exact identity validation.

For `private_external`, successful restore uses the supplied source as a temporary reconstruction input and does not copy it into the book workspace. It atomically replaces/reconstructs extracted artifacts and updates the manifest only when the reconstructed corpus matches the durable source identity.

Thus a private binary may be reattached for repair without becoming repository state.

## Same-name replacement protection

Filename is never sufficient provenance.

If a supplied or canonical source has the expected name but a different size or hash, verification/restore fails before extracted artifacts, metadata, or manifest are modified.

If size happens to match but SHA-256 differs, the hash mismatch still rejects it.

No command automatically updates durable source identity from a replacement binary. Changing source identity is a migration/re-import operation outside #11.

## Partial corpus behavior

For explicit-source books, any of the following makes corpus state invalid:

- missing manifest;
- missing manifest entry;
- extra manifest entry;
- chapter count mismatch;
- missing extracted file;
- path/number/title mismatch;
- extracted SHA mismatch;
- metadata/manifest source identity mismatch;
- required embedded source missing;
- present canonical source with incorrect identity.

`status` reports the reason and `resume` returns `blocked` without claiming or mutating workflow state.

## Compatibility boundary

This issue intentionally avoids silent migration.

Books without `metadata.source`:

- retain current metadata/source validation;
- may retain a legacy source manifest;
- may still appear `unsealed` in #10 status if no manifest exists;
- are not assigned `embedded` merely because a source file exists;
- require future #16 migration to opt into explicit source semantics.

Books with `metadata.source`:

- are governed by #11 explicit source semantics;
- require a valid sealed manifest;
- fail closed when the explicit contract is incomplete or inconsistent.

This distinction lets the current runtime preserve old pinned workspaces while making all newly created workspaces reproducible by construction.

## Schema changes

`scripts/workflow_v2/schemas.py` extends metadata validation for optional explicit `source` and source-manifest validation for the additive fields.

Unknown additive fields continue to be preserved under the existing schema policy.

Schema version remains `1` because these are additive fields with explicit enablement through `metadata.source`; legacy documents remain valid. A future incompatible representation would require a schema-version change or explicit migration.

## CLI surface

New user-visible option:

```bash
python scripts/book.py extract <source> ... --private-source
```

Existing corpus commands remain:

```bash
python scripts/corpus.py seal <book>
python scripts/corpus.py verify <book>
python scripts/corpus.py restore <book> <source>
```

For new explicit-source books, manual `seal` is normally unnecessary because extraction seals automatically. `seal` remains useful for legacy workspaces and repair workflows, but it may not silently change explicit source identity or storage mode.

No command is added solely to attach a private binary permanently.

## Testing strategy

TDD proceeds in small slices.

### Slice 1 — explicit source schema and initialization

RED tests first:

- new embedded extraction writes explicit source identity and a sealed manifest;
- private extraction writes the same durable identity but leaves no original binary in the workspace;
- explicit-source metadata/manifest mismatches fail schema/domain validation.

### Slice 2 — verification and resume gate

RED tests first:

- verified private corpus succeeds without original source binary;
- missing/partial/tampered extracted corpus fails verification;
- missing manifest for an explicit-source book is invalid, not unsealed;
- `book.py resume` returns `blocked` for those invalid states;
- legacy unsealed behavior remains unchanged.

### Slice 3 — exact restore/reattachment

RED tests first:

- same-name/different-hash source is rejected before writes;
- wrong-size source is rejected before writes;
- exact private source reconstructs corpus without persisting the binary;
- embedded restore still persists the exact source as appropriate;
- failed restore leaves prior extracted/state/manifest content unchanged.

### Slice 4 — status reporting

RED tests first:

- JSON status reports storage mode, source identity, attachment state, and verified chapter count deterministically;
- human status clearly identifies private versus embedded reproducibility;
- status remains read-only.

After each RED/GREEN slice, run the focused tests and then the complete test suite on supported Python versions through the existing PR CI matrix.

## Expected implementation files

The intended minimal implementation surface is:

```text
scripts/book.py
scripts/corpus.py
scripts/workflow_v2/schemas.py
scripts/workflow_v2/status_cli.py

tests/test_workflow_v2_private_source.py
tests/test_corpus_cli.py              # only where existing restore behavior is extended
tests/test_workflow_v2_status_cli.py  # only for source status/resume integration
```

Additional files should be added only if a concrete test exposes a responsibility that cannot remain clear in these existing boundaries.

## Relationship to later issues

#11 provides #12 with verified machine-readable source reproducibility state but does not implement finalization.

#16 may later migrate legacy workspaces into the explicit source contract.

#17 may later transport the same durable metadata/manifest through a GitHub API backend without changing source-integrity semantics.

The authoritative split remains:

- metadata: declared original source identity and storage policy;
- source manifest: sealed extracted-corpus evidence bound to that identity;
- filesystem artifacts: current bytes to verify;
- status: read-only computed view;
- future generated reports: derived, non-authoritative presentation.
