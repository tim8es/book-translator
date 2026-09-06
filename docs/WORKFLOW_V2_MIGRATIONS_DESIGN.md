# Workflow v2 — migrations and compatibility design (#16)

## Goal

Allow an existing book workspace to adopt the currently installed Workflow v2 revision only through an explicit, recoverable command. Preserve pinned semantics before that command, never fabricate provenance/review evidence, and never leave an interrupted upgrade in an unrecoverable mixed state.

## Scope

- Add `book.py workflow-upgrade <slug> --to <revision>`.
- Add explicit versioned migration planning for metadata, progress, review ledger, claims and source manifest durable JSON.
- Treat missing `schema_version` as logical schema v0 only inside the explicit upgrade path.
- Preserve current read-only legacy compatibility for ordinary status/validate/load paths; no background/silent writes.
- Validate legacy state before mutation, construct the complete target state in memory, validate target state, then apply through backend-neutral CAS.
- Add durable crash recovery/rollback for multi-document migration.
- Record previous/new workflow revisions in metadata only after all other target documents are durably migrated.
- Extend #18 with migration failure-injection/idempotence coverage.

Out of scope:

- arbitrary cross-repository migrations;
- downloading or executing migration code from a requested revision;
- migrations to unknown future schema versions;
- GitHub API backend implementation (#17);
- parallel-mode proposal migration (#15);
- rewriting generated Markdown/output artifacts during upgrade.

## Existing compatibility boundary

Current schema version is v1. Normal repository reads accept missing `schema_version` only for metadata/progress when `allow_legacy=True`, normalize it in memory, and never persist that normalization. Explicit unsupported versions fail. Non-legacy schema families require an explicit v1 version.

#16 preserves that behavior outside `workflow-upgrade`.

## Chosen architecture

Use a versioned pure migration registry plus a transient durable migration journal.

Alternatives rejected:

1. Hard-code one procedural v0→v1 CLI path. Smaller initially, but not safely extensible and mixes planning, I/O and recovery.
2. Persist the result of existing `allow_legacy=True` reads automatically. Rejected because it creates silent upgrades and cannot safely cover multi-document state.

### Components

- `workflow_v2/migrations.py`: version detection, pure migration steps, compatibility planning, journal model and transaction executor. No argparse and no filesystem-specific logic.
- `workflow_v2/migrations_cli.py`: resolve book/runtime provenance, register `workflow-upgrade`, render deterministic JSON/human result.
- `schemas.py`: add only the transient migration-journal schema kind/validator required for authoritative crash recovery. Existing v1 validators remain authoritative for migrated documents.
- Existing `StorageBackend` / `WorkflowStateRepository`: all writes use `create_if_absent`, `write_if_version` or `delete_if_version`.

## Version model

### Logical v0

For metadata, progress, review ledger, claim and source manifest, v0 means:

- the document is JSON object data;
- `schema_version` is absent;
- every other field needed by the v1 validator is already present and valid after adding `schema_version: 1`.

The migration registry may not invent missing claim identity, hashes, workflow revisions, review provenance or source identity.

### v0→v1 pure step

For each supported kind:

1. deep-copy the legacy mapping;
2. add `schema_version: 1`;
3. run the normal strict v1 validator;
4. return canonical target data.

If validation fails, return a precise compatibility error identifying document/path and missing/incompatible field. No durable mutation occurs.

### Explicit v1

Already-v1 documents are validated and retained byte-semantically; upgrade planning may still update metadata workflow provenance/history at the end.

Explicit schema versions other than 1 are unsupported by this release and fail before any write.

## Runtime target and pinned provenance

`workflow-upgrade --to <revision>` never treats user text as proof that migration code for that revision is installed.

The CLI resolves the current installed workflow provenance from `.book-translator-install.json` using the same canonical fields as extraction:

- canonical repository;
- requested ref;
- resolved revision.

Requirements:

- installed `resolved_revision` must be present;
- `--to` must exactly equal that installed resolved revision;
- metadata workflow repository, when present, must refer to the canonical repository;
- an already pinned book remains pinned until this explicit command succeeds.

A target that differs from the installed revision fails before writes. This prevents false provenance claims.

## Upgrade history

On successful upgrade, `metadata.workflow` is updated last and contains the installed repository/requested-ref/resolved-revision plus append-only `upgrade_history`.

Each new history entry is deterministic data:

```json
{
  "from_revision": "old-revision-or-null",
  "to_revision": "installed-revision",
  "schema_versions": {
    "metadata": {"from": 0, "to": 1},
    "progress": {"from": 0, "to": 1},
    "review_ledger": {"from": 0, "to": 1},
    "source_manifest": {"from": 0, "to": 1},
    "claims": {"from": 0, "to": 1}
  }
}
```

Only families actually present/migrated are included. No wall-clock timestamp is required; Git/state revisions remain secondary audit evidence. Re-running an already completed upgrade to the same installed revision is a deterministic no-op and does not append another history entry.

## Document discovery and raw reads

Upgrade planning cannot use only schema-aware repository reads because legacy review ledgers/claims/source manifests intentionally fail current parsing.

The migration planner reads exact bytes through `StorageBackend`, decodes strict UTF-8 JSON and records the storage revision for every candidate document. It discovers:

- `metadata.json` (required);
- `progress.json` (required);
- `review-ledger.json` (optional for legacy workspace);
- `source-manifest.json` (optional for legacy workspace);
- `.workflow/claims/*.json` (zero or more).

Malformed UTF-8/JSON fails before mutation.

## Review compatibility

Machine review evidence must never be fabricated.

Rules:

- If a review ledger exists, it must be explicit/v0-compatible and every migrated record must validate as v1.
- If the legacy book has no review ledger, create an empty v1 ledger for the same book slug.
- For every chapter whose progress status is `reviewed`, resolve current review evidence against exact source/translation bytes using the migrated v1 ledger.
- If current PASS exists, `reviewed` may remain.
- If current PASS is missing/stale/unprovable, migrate that chapter to `translated` when a non-empty translation artifact exists.
- If a supposedly reviewed unit has no valid translation artifact, compatibility fails instead of inventing lifecycle state.

Thus legacy human-reviewed work can be retained as translation content, but it must pass the normal Reviewer flow before becoming machine-reviewed again.

## Source/corpus compatibility

Source integrity is also never fabricated.

If `source-manifest.json` exists, its v0/v1 form must validate after migration and its hashes must match current source/extracted bytes under the normal corpus verifier.

If the manifest is absent:

- embedded source: build a v1 source manifest only from the actual declared source file and all referenced extracted artifacts using `build_source_manifest`; exact bytes/hashes become the authority;
- explicit `private_external`: an absent source binary can be accepted only if metadata already contains complete explicit source identity (`filename`, `size_bytes`, `sha256`) and the extracted corpus can be sealed without inventing source bytes. The migration builds the manifest identity from that explicit metadata plus exact extracted hashes;
- if source identity or any extracted artifact cannot be proven, compatibility fails before writes.

The planner then runs the standard corpus verification against the candidate migrated manifest.

## Claims compatibility and admission gate

Upgrade is a book-wide state transition.

Before mutation:

- active finalization marker blocks upgrade;
- active/live claims block upgrade;
- expired claims may be migrated as durable audit-relevant state, but are not revived or extended;
- malformed/unprovable legacy claims fail compatibility.

Upgrade obtains the existing book coordination mutex with operation `workflow_upgrade` before creating the migration journal. Claim admission therefore cannot race into the transition.

After acquiring the mutex, the planner re-reads all candidate document revisions and admission state. Any mismatch aborts before target writes.

## Durable migration journal

Add `.workflow/migration.json`, schema v1, transient authoritative recovery state.

It contains:

- `schema_version: 1`;
- `operation: "workflow_upgrade"`;
- book slug;
- from/to workflow revision;
- phase: `prepared` or `applied`;
- ordered document entries.

Each document entry contains:

- path;
- schema kind/family;
- whether the path existed originally;
- original storage revision or null;
- SHA-256 of exact original bytes or null;
- base64 of exact original bytes or null;
- SHA-256 of canonical target bytes;
- resulting storage revision once written, or null.

The journal contains enough information to restore exact original JSON bytes without reparsing/re-serializing them.

Generated reports/EPUB outputs are not journaled because #16 does not rewrite them; after a successful upgrade they may naturally resolve stale through existing status/build logic.

## Transaction order

All target data is computed and validated before journal creation.

Write order is deterministic:

1. source manifest (create/update if needed);
2. review ledger (create/update if needed);
3. claims sorted by path;
4. progress;
5. metadata **last**.

For every existing document, write with the exact version captured during planning. New documents use create-if-absent.

After each write, update the journal entry with the resulting revision using journal CAS.

Metadata is last because it is the externally visible workflow pin. A book is not considered upgraded until metadata points at the new revision and the journal verifies every target hash.

## Crash and conflict recovery

When `workflow-upgrade` starts and `.workflow/migration.json` exists, it recovers before planning a new transaction.

For every journal entry, read current exact bytes and classify:

- equal target hash: target write completed;
- equal original hash / originally absent: write not yet applied;
- anything else: unknown concurrent mutation → fail closed, preserve journal for manual inspection.

Recovery behavior:

- all documents target + metadata target: validate full migrated state, mark journal `applied`, then delete journal with CAS; return success/no-op;
- mixture only of known original/target states: restore every target-written path to its exact original bytes (or delete paths originally absent), metadata first during rollback, then other documents in reverse application order; verify original hashes; delete journal; then restart migration from fresh state;
- unknown mutation: do not overwrite it and do not delete the journal.

A normal CAS/write failure follows the same rollback path before returning an error. Failed migration therefore leaves the previous valid state usable whenever no unrelated concurrent mutation occurred.

## Final validation

Before metadata write, validate candidate documents individually and cross-document invariants:

- metadata/progress book shape;
- review ledger book slug and reviewed/PASS safety;
- source manifest/corpus integrity;
- claims reference canonical existing units and retain original lease/provenance data.

After metadata write, re-read the durable candidate state through normal strict v1 APIs and rerun the same invariants. Only then may the journal be removed.

## CLI contract

`book.py workflow-upgrade <slug> --to <revision> [--json]`

Success JSON/human result includes:

- book slug;
- `from_revision`;
- `to_revision`;
- changed/no-op;
- migrated document paths/families;
- lifecycle downgrades (`reviewed` → `translated`) when review evidence could not be proven.

Expected compatibility/conflict/recovery failures are concise errors without traceback and exit non-zero.

The command never rewrites a book unless explicitly invoked.

## TDD slices

1. Pure version detection/migration registry and precise compatibility errors.
2. Compatibility planner: installed target provenance, source reconstruction, review downgrade, claims/finalization gates.
3. Durable transaction/journal: deterministic order, metadata-last, CAS rollback, crash recovery/idempotence.
4. CLI `workflow-upgrade` end-to-end with representative legacy fixtures and no-op rerun.
5. #18 migration reliability: crash after intermediate write, CAS conflict, unknown concurrent mutation fail-closed, successful recovery.
6. Full Python 3.10/3.12 matrix and final diff/review/ancestry audit.

## Acceptance criteria

- No silent write/upgrade occurs from normal reads/status/validate.
- An old pinned workflow stays pinned until explicit `workflow-upgrade` succeeds.
- `--to` must match the actually installed resolved revision.
- v0-compatible metadata, progress, review ledger, claims and source manifest migrate deterministically to v1.
- Unknown/malformed legacy shapes fail precisely before mutation.
- Legacy reviewed state without current machine PASS becomes translated, never fabricated reviewed/PASS.
- Missing source manifest is reconstructed only from provable source identity and exact corpus bytes; otherwise upgrade fails.
- Live claims/finalization prevent upgrade admission.
- Successful upgrade records previous/new workflow revisions and does not duplicate history on rerun.
- Failed/interrupted migration restores the previous exact durable state when changes are known; unknown concurrent mutation is never overwritten.
- Recovery is deterministic from a fresh process using only repository state.
- Standard Python suite covers migration behavior without GitHub Actions being required for execution.
- PR targets only `refactor/workflow-engine-v2`; `main` remains unchanged.
