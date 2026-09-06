# Workflow v2 EPUB Output Design (#14)

## Goal

Make EPUB a deterministic, validated Workflow v2 deliverable rather than a manual packaging step. Preserve the existing Markdown build path and keep generated artifacts subordinate to authoritative repository state.

## Scope

- Extend `book.py build <slug>` with `--format markdown|epub`; default remains Markdown.
- EPUB builds use canonical `progress.json` chapter order.
- Normal builds require `reviewed` lifecycle plus current PASS evidence; `--allow-unreviewed` remains explicit preview mode and permits `translated`/`reviewed` units without claiming final-review completion.
- Generate a deterministic EPUB 3 package with metadata, language, nav/TOC, spine, CSS, chapter XHTML and optional cover.
- Validate every generated EPUB before reporting success.
- Write deterministic `output/manifest.json` describing the artifact and the exact build inputs.
- Add read-only `book.py build-status <slug> --format epub [--json]` returning `missing`, `current`, `stale` or `invalid`.
- Extend #18 reliability coverage for incomplete builds, interrupted/stale outputs and idempotent rebuilds.

## Architecture

### Domain module

Add `scripts/workflow_v2/epub_output.py` with no argparse dependency. It owns:

- safe path validation for output/cover paths;
- Markdown-to-XHTML rendering for the supported translation subset;
- deterministic EPUB assembly;
- EPUB structural validation;
- build-input snapshot/fingerprint construction;
- output manifest construction and validation;
- current/stale/invalid output resolution.

The module uses only Python stdlib (`zipfile`, `xml.etree.ElementTree`, `html`, `hashlib`, `json`, `pathlib`, `io`). No third-party runtime dependency is introduced.

`output/manifest.json` is intentionally **not** registered as an authoritative `SchemaKind`: it is a generated delivery projection. Its strict v1 shape is validated by `epub_output.validate_output_manifest()` before use. This keeps migration semantics for machine workflow state separate from generated output metadata.

### CLI adapter and preflight

Keep `book.py` as the user-facing parser and existing Markdown builder. Add a thin `workflow_v2/epub_cli.py` adapter that registers/executes the EPUB/build-status branch and translates expected failures into the existing concise CLI error surface.

The existing `build` command remains backward-compatible:

- no `--format` means `markdown`;
- `--output` remains supported for Markdown and EPUB, but the extension must match the selected format;
- `--allow-unreviewed` remains the explicit preview gate.

EPUB build must not rely on raw `book.validate_book()` alone, because explicit `private_external` books intentionally have no source binary in `books/<slug>/source/`. The CLI reuses the same normalized structural + corpus preflight path used by status/finalize (`status_cli.default_preflight` or an equivalent shared adapter):

- structural errors after explicit-source normalization must be empty;
- corpus state must be `verified`;
- final build additionally requires every included unit to be `reviewed` with current PASS evidence;
- preview build requires each included unit to be `translated` or `reviewed`, but does not claim review completion.

`book.py build-status` is read-only and never rewrites manifests or artifacts.

## EPUB package contract

The deterministic package contains:

- `mimetype` as the first ZIP member, stored uncompressed, exact bytes `application/epub+zip`;
- `META-INF/container.xml`;
- `EPUB/package.opf`;
- `EPUB/nav.xhtml`;
- `EPUB/styles.css`;
- one XHTML document per included unit in canonical order;
- optional cover asset when `metadata.cover_path` is present, referenced from OPF with EPUB 3 `cover-image` semantics.

`package.opf` uses EPUB 3.0 metadata. Required metadata:

- title from `metadata.title`;
- creator when `metadata.author` is non-empty;
- language from `metadata.target_language`;
- deterministic identifier derived from book slug + build-input fingerprint;
- nav manifest entry, CSS, ordered chapter items, optional cover item.

No wall-clock modified timestamp is used. ZIP entry timestamps are pinned to a constant DOS-compatible value so identical inputs produce identical EPUB bytes.

## Markdown-to-XHTML subset

Translations are already stored as Markdown. The builder converts a deterministic safe subset:

- ATX headings `#` through `######`;
- paragraphs separated by blank lines;
- unordered list lines beginning `- ` or `* `;
- ordered list lines beginning `<number>. `;
- fenced code blocks;
- all remaining text escaped as plain text.

Inline Markdown interpretation is intentionally not added in #14. Raw HTML is escaped rather than executed. This keeps output deterministic and avoids introducing a Markdown dependency.

Each chapter must render at least one non-empty body element or build fails.

## Optional cover

`metadata.cover_path` is optional and, when present:

- must be a safe relative path inside the book workspace;
- must exist and be a regular file;
- supported extensions are `.jpg`, `.jpeg`, `.png`, `.gif`, `.svg`;
- its exact bytes participate in the build fingerprint;
- it is copied into the EPUB with the correct media type and referenced from OPF as the cover image.

Existing books without `cover_path` need no migration.

## Build-input snapshot and fingerprint

The build-input snapshot is deterministic data derived only from relevant inputs:

- build contract version (`epub-build-v1`);
- book slug;
- selected format and preview mode;
- metadata fields affecting EPUB bytes: title, author, target language, cover path;
- workflow resolved revision;
- ordered units: unit id/number/title/translation path/status + exact translation SHA-256;
- for final builds, normalized current review identity per unit: resolved state, exact source/translation hashes, workflow revision and review-contract revision;
- optional cover SHA-256;
- explicit build configuration version.

Raw storage revision tokens are **not** part of the fingerprint. In particular, `review-ledger.json` revision is not a direct fingerprint input: appending semantically duplicate PASS evidence must not stale otherwise identical output. Final-build review identity is derived through the same current-resolution logic as #9/#21.

The fingerprint is SHA-256 of canonical JSON for that snapshot.

Repository/storage revisions are still recorded separately in the manifest as provenance. Repository HEAD is best-effort (`repository_commit`, nullable) and does not participate in staleness because unrelated commits must not invalidate an otherwise identical artifact.

## Output manifest

`books/<slug>/output/manifest.json` is generated only after the candidate EPUB validates successfully. Strict domain-validated schema:

```json
{
  "schema_version": 1,
  "build_contract": "epub-build-v1",
  "book_slug": "sample",
  "format": "epub",
  "preview": false,
  "artifact_path": "output/sample.epub",
  "artifact_sha256": "...",
  "unit_count": 12,
  "input_fingerprint": "...",
  "repository_commit": "... or null",
  "state_revisions": {
    "metadata": "...",
    "progress": "...",
    "review_ledger": "..."
  }
}
```

The manifest is a generated projection, not authoritative workflow state.

If an existing manifest+artifact already resolve as `current`, an identical rebuild returns unchanged and preserves the existing manifest bytes. This prevents an unrelated new Git HEAD from causing provenance-only churn.

## Staleness semantics

`build-status` recomputes the current input snapshot/fingerprint and validates the stored manifest/artifact:

- `missing`: manifest or artifact is absent;
- `current`: manifest validates, artifact exists and validates, artifact SHA matches, input fingerprint matches current relevant inputs;
- `stale`: manifest/artifact are structurally valid but the current relevant input fingerprint differs;
- `invalid`: manifest malformed/unsupported, path unsafe, artifact hash mismatch, EPUB invalid or manifest/artifact identity inconsistent.

Relevant mutations that must produce `stale`: translation bytes, title/author/target language, chapter order/title/path/status, current review identity for final builds, cover path/bytes, workflow/build contract configuration.

Changing an unrelated repository file/commit alone does not produce `stale`.

## Write and failure semantics

- Build performs read/preflight/render/assemble/validate entirely before replacing the final EPUB.
- Artifact is written with temp-file + atomic replace semantics through the filesystem adapter.
- Manifest is written only after the final artifact is present and revalidated.
- If candidate generation/validation fails, the prior valid artifact/manifest are left untouched.
- If process death occurs after artifact replacement but before manifest replacement, `build-status` returns `invalid` or `stale`, never `current`; rerun rebuilds deterministically.
- Identical successful rebuilds detect equal bytes and do not rewrite artifact/manifest.

## Validation contract

The validator rejects unless all are true:

- ZIP opens successfully;
- `mimetype` exists, is first, stored, exact content;
- `META-INF/container.xml` parses and references an existing OPF;
- OPF parses as EPUB package document;
- title/language present;
- manifest contains nav, CSS and all expected chapter items;
- nav XHTML parses and links every chapter in order;
- spine count equals expected unit count and every `idref` resolves;
- every chapter XHTML parses and has non-empty body text/content;
- optional cover reference resolves and media type matches its supported extension.

## Integration with #12

A normal final build after `book.py finalize` naturally sees all chapters as `reviewed` and current PASS evidence. #14 does not parse `STATE.md`, `FINAL_QUALITY_GATES.md` or `REVIEW_REPORT.md`; authoritative metadata/progress/review ledger and exact artifact bytes remain the source of truth.

Preview builds may use translated-but-unreviewed units, are marked `preview: true`, and do not count as a final deliverable.

## TDD slices

1. Domain snapshot/fingerprint + strict generated-manifest validation + stale resolution.
2. Deterministic EPUB assembly + validator.
3. CLI `build --format epub`, private-source preflight, preview gate, output manifest and `build-status`.
4. #18 reliability: incomplete build, interrupted artifact/manifest window, stale relevant input, semantically duplicate review evidence, unrelated Git commit and identical rebuild idempotence.
5. Full matrix CI and final diff/review/ancestry audit.

## Acceptance criteria

- One command builds and validates a readable EPUB from a complete reviewed book.
- Existing Markdown build behavior remains compatible.
- `private_external` final builds work without persisting the private source binary.
- Output manifest identifies exact relevant workflow state and artifact hash.
- Relevant source/review state changes are reported stale; semantically duplicate review evidence and unrelated commits are not.
- Incomplete/unreviewed final build fails unless preview mode is explicit.
- Generated EPUB and manifest are byte-identical on unchanged successful rebuild.
- Failure/interruption never reports a mismatched output as current.
- Standard Python test suite covers all behavior without requiring GitHub Actions or external services.
