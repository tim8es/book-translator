# Source-bound structural translation

The reusable, dependency-free helper is in scripts/workflow/structural_translation.py.
It handles EPUB-derived Markdown units with external/internal links, linked
images and Book Translator EPUB marker comments.

## Responsibility and pinning

Literary roles and acceptance contracts remain at each book's immutable
metadata.json.workflow.resolved_revision. Structural translation is an
orchestrator-owned runtime adapter, not a workflow upgrade. The adapter loads
a separately pinned **public helper commit** from the canonical repository,
and records that helper SHA in the job's durable provenance.

The helper partitions extracted Markdown into ordered text spans and immutable
protected constructs. A model never receives an instruction to reproduce
technical URL strings, fragment IDs, linked image paths or EPUB markers.
The current Translator contract requires existing link labels to remain
unchanged, so complete original Markdown link/image elements are preserved.

The translator receives bounded spans with stable IDs and returns only the
translated content of those spans, alongside ordinary glossary/style decisions.
Valid partial responses can be staged with source SHA-256 and both revision
pins. No canonical translation is changed until all text spans are covered
and a deterministic assembly passes structural checks. Existing
accept-translation and independent Reviewer PASS are still mandatory.

## Safety and economics

- A missing/invalid model segment is retried within the bounded model budget.
  Previously accepted segments are not regenerated.
- Stage evidence is invalidated if source, workflow or helper revision changes.
- URL bytes, protected markers and linked images are never guessed from
  failed model responses and never reconstructed heuristically.
- Segmentation can constrain word ordering around inline links; independent
  source-comparison review still evaluates fidelity and style.
- Chapters without protected links/markers retain the existing full-unit
  translation path, avoiding unnecessary token and Actions overhead.
- Source ingestion and coverage verification still own safe conversion into
  verified Markdown units; the helper does not reinterpret raw source formats.

## Structured protocol recovery and coverage gate

Callers may use `protocol_failure_code` to classify a rejected model output
as a repairable protocol error. Transport, source, plan and persisted-state
errors are not repairable by this path. `repair_guidance` generates one focused
single-span JSON instruction with the canonical source ID. A caller must
durably reserve the additional paid model invocation before retrying, preserve
already accepted segments, and never relax `parse_batch` or `render`.

The pure `coverage_alert` is a conservative truncation alarm for long units:
both length and paragraph coverage must be substantially below the source.
It is a fail-closed pre-acceptance indicator, not proof of semantic
completeness; independent review remains mandatory.

## Tests

Tests in tests/test_structural_translation.py cover external/internal and
inline/trailing/repeated links, linked images, EPUB markers, partial segments,
invalid IDs, source hashes and malicious markup injection.
The book-state adapter requires separate job checkpoint and queue tests.
