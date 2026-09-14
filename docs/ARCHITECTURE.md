# Architecture

Book Translator has one supported workflow runtime.

The current workflow is repository-authoritative and uses one durable state contract: schema-versioned metadata/progress, explicit source identity and corpus manifest, durable claims, machine review evidence, coordination, finalize/build state, and backend-neutral compare-and-swap storage.

## Supported runtime

- `scripts/book.py` is the primary CLI entrypoint.
- `scripts/corpus.py` owns source-corpus integrity and restore operations.
- `scripts/workflow_v2/` is the internal workflow package. The `v2` suffix is an implementation-era module name, not a second supported workflow version.
- `docs/ORCHESTRATION.md` and `docs/TRANSLATION.md` are the canonical execution contracts.
- Files under `books/<slug>/` are authoritative durable book state.

## State contract

Every supported book workspace uses the current schema and must contain current workflow provenance, explicit source identity, a sealed source manifest, and machine review evidence. Missing schema versions, legacy lifecycle-only review state, unsealed legacy source state, and workflow migration journals are not supported runtime modes.

An older workspace must be converted outside the production runtime before it is used. The current runtime does not auto-normalize legacy schemas and does not perform workflow-version migrations.

## Runtime boundaries

The orchestration core owns state transitions. Translator and Reviewer roles produce artifacts and decisions but do not race shared mutable state. Storage backends provide create-if-absent and compare-and-swap semantics; filesystem and GitHub API storage implement the same contract.

Source integrity is independent from literary review. A valid source corpus does not imply a reviewed translation, and a Reviewer PASS does not replace structural or source-integrity validation.

## Completion

A book is complete only when all intended units have current machine-verifiable PASS evidence, durable lifecycle state is reviewed, structural and corpus checks pass, and requested output artifacts are built and verified.
