# Workflow v2 — migrations and compatibility design (#16)

## Goal

Allow an existing book workspace to adopt the currently installed Workflow v2 revision only through an explicit, recoverable command. Preserve pinned semantics before that command, never fabricate provenance/review/source identity, and never leave an interrupted upgrade in an unrecoverable mixed state.

## Scope

- Add `book.py workflow-upgrade <slug> --to <revision>`.
- Add explicit versioned migration planning for metadata, progress, review ledger, claims and source manifest JSON.
- Treat missing `schema_version` as logical schema v0 only inside the explicit upgrade path.
- Keep normal legacy reads/status/validate non-mutating.
- Validate legacy state, build all target state in memory, validate it, then apply through backend-neutral CAS.
- Add durable rollback/recovery for multi-document migration.
- Update metadata workflow provenance last.
- Make unfinished migration visible to fresh status/resume and block competing workflow transitions.
- Extend #18 with migration failure-injection/idempotence coverage.

Out of scope: #17 GitHub backend, #15 parallel proposals, unknown future schema migrations, downloading/executing migration code from another revision, and rewriting generated reports/EPUB artifacts.

## Existing compatibility boundary

Current durable workflow schema version is v1. Outside `workflow-upgrade`, missing `schema_version` is accepted only for metadata/progress through existing `allow_legacy=True`, normalized in memory, and never written automatically. Explicit unsupported versions fail. #16 preserves that boundary.

## Chosen architecture

Use a pure migration registry + compatibility planner + transaction executor + transient durable migration journal.

Rejected alternatives:

1. One procedural v0→v1 CLI function: too coupled and weak for recovery/future migration steps.
2. Persist existing `allow_legacy=True` normalization: violates the no-silent-upgrade requirement.

### Components

- `workflow_v2/migrations.py`: pure version migration, raw discovery, compatibility planning, transaction executor.
- `workflow_v2/migration_journal.py`: isolated strict validation/loading/serialization for `.workflow/migration.json`. It depends only on storage-safe primitives and does not import review/finalize/status modules.
- `workflow_v2/migrations_cli.py`: installed-provenance resolution and CLI registration.
- `source_integrity.py`: private-external manifest reconstruction from already-recorded source identity + exact extracted bytes.
- `coordination.py`: allow `workflow_upgrade` operation and expose active migration marker.
- claims/finalize/review/status: block or route around active migration journal.

The migration journal is transient orchestration state, not one of the normal versioned book-document schemas. It is therefore validated by `migration_journal.py`, not added to `SchemaKind`; this avoids coupling the central schema registry to recovery implementation details while still validating the journal strictly on every read/write.

## Version model

### Logical v0

For metadata, progress, review ledger, claim and source manifest, v0 means:

- JSON object;
- no `schema_version` field;
- every other field needed by the v1 validator is already present and valid after adding `schema_version: 1`.

Migration may not invent missing claim IDs, hashes, workflow revisions, review provenance or source identity.

### v0 → v1

`migrate_document(kind, data)`:

1. deep-copy mapping;
2. add `schema_version: 1`;
3. run the ordinary strict v1 validator;
4. return canonical target mapping.

Incomplete or incompatible legacy data fails with a path/kind-specific compatibility error before durable mutation.

Explicit v1 is strictly validated and not rewritten unless cross-document repair/provenance actually changes it. Explicit versions other than 1 are unsupported.

## Runtime target and pinned provenance

`workflow-upgrade --to <revision>` is only allowed when `<revision>` exactly matches the installed `.book-translator-install.json` `resolved_revision`.

Installed provenance must contain canonical repository + resolved revision. Existing metadata workflow repository, when present, must refer to the canonical repository. User-supplied text alone is never treated as proof that migration code for another revision is installed.

An old book stays pinned until the explicit upgrade transaction succeeds.

## Upgrade history and no-op

Changed upgrade writes metadata last with:

- installed repository;
- installed requested ref;
- installed resolved revision;
- `review_evidence: "review-ledger-v1"`;
- append-only deterministic `upgrade_history` entry containing `from_revision`, `to_revision` and migrated schema-family from/to versions.

No wall-clock field is required.

A true no-op requires all of:

- no migration journal;
- targeted durable state already strict v1 and cross-document valid;
- no source/review repair required;
- metadata already pinned to requested installed revision with v1 review marker.

A no-op rewrites nothing and appends no history. If schemas still need migration even when from/to revision strings match, it remains a changed schema upgrade.

## Raw discovery

Planner reads exact bytes/revisions directly through `StorageBackend` because legacy ledger/claim/manifest may not pass current repository parsing.

Required/optional paths:

- `metadata.json` required;
- `progress.json` required;
- `review-ledger.json` optional;
- `source-manifest.json` optional;
- `.workflow/claims/*.json` zero or more.

UTF-8/JSON errors fail before writes. Original exact bytes and storage revisions are retained in the plan.

## Review compatibility

Machine review evidence is never fabricated.

- Existing ledger must be v0-compatible or strict v1.
- Missing ledger produces an empty v1 candidate ledger for the same book slug.
- Every `reviewed` unit is re-resolved against exact current source/translation bytes and candidate ledger.
- Current PASS preserves `reviewed`.
- Missing/stale/unprovable PASS downgrades to `translated` if a non-empty translation exists.
- Missing/empty translation for a supposedly reviewed unit is a compatibility error.

Legacy human review therefore preserves translation content but must pass the normal Reviewer flow before machine-reviewed lifecycle can be claimed again.

## Source compatibility

Source identity is never fabricated.

Existing source manifest must migrate/validate and agree with metadata + actual source/extracted bytes.

If absent:

- embedded source: build v1 manifest from actual declared source file + all referenced extracted files;
- explicit `private_external`: original binary may remain absent only when metadata already contains complete source identity (`filename`, `size_bytes`, `sha256`, storage mode). Manifest source identity is copied from metadata; extracted hashes are computed from exact files;
- missing source identity or extracted artifact fails before writes.

Candidate corpus is checked with the same source-integrity policy used by normal status/finalize.

## Claims and admission

Before mutation:

- active finalization blocks upgrade;
- live claims block upgrade;
- expired claims may be schema-migrated but are not revived/extended;
- malformed legacy claims fail compatibility.

Claim expiry uses injected UTC clock for deterministic tests.

Upgrade acquires the existing coordination mutex with `operation="workflow_upgrade"`; #16 extends the coordination validator/manager allowed-operation set. After acquiring it, planner/executor rechecks captured revisions/admission state before writes.

## Active migration visibility

`.workflow/migration.json` remains authoritative after the short coordination lease expires.

While it exists:

- new claim acquisition rejects;
- finalize rejects;
- `accept_review` rejects;
- status exposes bounded migration recovery state;
- resume prioritizes `operation="workflow_upgrade"` rather than ordinary translate/review/finalize dispatch.

Malformed journal causes fail-closed status/recovery. Read-only status/resume never mutate it.

## Migration journal contract

Path: `.workflow/migration.json`.

`migration_journal.py` validates exactly:

- `schema_version: 1`;
- `operation: "workflow_upgrade"`;
- non-empty book slug;
- `from_revision` null/non-empty string;
- non-empty `to_revision`;
- `phase: "prepared" | "applied"`;
- ordered `documents` array.

Each entry:

- safe relative path;
- known schema-family string;
- `original_exists` boolean;
- original storage revision/hash/base64 exact bytes when originally present, otherwise all three null;
- target SHA-256;
- resulting revision null while not known, non-empty string after write.

For `phase="applied"`, every entry has a resulting revision. Base64 must decode strictly and hash to `original_sha256`.

Journal stores enough data to restore exact original JSON bytes without reparsing/reserializing them.

## Transaction order

All target data is built/validated before journal creation.

Apply order:

1. source manifest;
2. review ledger;
3. claims sorted by path;
4. progress;
5. metadata last.

Existing documents use captured CAS revisions; new documents use create-if-absent. After each target write, journal is CAS-updated with resulting revision. Crash after target write but before journal update remains recoverable via byte hashes.

Metadata is the externally visible workflow pin and therefore writes last.

## Crash/conflict recovery

On command start, existing journal is recovered before a new plan.

Recovery first acquires `workflow_upgrade` coordination. If a crashed process still holds an unexpired coordination lease, return deterministic conflict; normal expiry allows later recovery.

For every journaled path classify current state:

- `target`: current bytes hash to target;
- `original`: current bytes hash to original, or path remains absent when originally absent;
- `unknown`: anything else.

Rules:

- all target: strict full-state validation, delete journal with CAS, return `recovered`;
- known mixture of target/original: restore target-written paths to exact originals (delete paths originally absent), metadata first, remaining paths in reverse apply order; verify originals; delete journal; re-plan and execute;
- unknown: mutate nothing, preserve journal, fail closed.

Normal CAS/apply failure uses the same rollback classifier. Rollback itself is crash-safe because the journal survives and every path remains classifiable as target/original.

## Final validation

Before metadata write and again after it:

- metadata/progress structural validity;
- review ledger book slug/current reviewed-PASS safety;
- source manifest/corpus integrity;
- claims map to canonical existing units and preserve original lease/provenance.

Journal is deleted only after post-write strict validation.

## CLI

`book.py workflow-upgrade <slug> --to <revision> [--json]`

Result contains slug, from/to revision, `changed|unchanged|recovered`, migrated paths/families and reviewed→translated downgrade chapter numbers. Expected compatibility/conflict/recovery errors are concise and traceback-free.

## TDD slices

1. Pure migration registry + domain-local journal validation.
2. Compatibility planner: target provenance, review downgrade, source reconstruction, claim/finalization gates.
3. Coordination + journaled transaction/rollback/recovery.
4. Active migration visibility/admission guards.
5. CLI end-to-end + representative legacy fixtures/no-op.
6. #18 migration crash/CAS/unknown-mutation/idempotence reliability.
7. Full Python 3.10/3.12 CI + diff/review/ancestry audit.

## Acceptance criteria

- No silent upgrade from ordinary reads/status/validate.
- Old pinned workflow remains pinned until explicit successful command.
- Target must equal installed resolved revision.
- v0-compatible metadata/progress/ledger/claims/source manifest migrate deterministically to v1.
- Unknown/malformed legacy shapes fail precisely before mutation.
- Reviewed state without machine PASS becomes translated, never fabricated PASS.
- Source manifest reconstruction uses only provable source identity/exact corpus bytes.
- Live claims/finalization block upgrade; expired claims are not revived.
- Active migration blocks competing workflow mutations and is visible to fresh resume.
- Successful upgrade records old/new revision and true no-op does not duplicate history.
- Interrupted/failed migration restores exact prior known state; unknown concurrent bytes are never overwritten.
- Recovery is deterministic from a fresh process using repository state only.
- Standard Python suite covers migration behavior; GitHub Actions is optional CI, not runtime.
- Merge only to `refactor/workflow-engine-v2`; `main` unchanged.
