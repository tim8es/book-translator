# Workflow v2 — Claims, leases and compare-and-swap concurrency

Issue: #8
Branch: `feature/workflow-v2-claims-cas`
Base while #7 is pending: `feature/workflow-v2-state-core`
Final PR target: `refactor/workflow-engine-v2`
Date: 2026-09-05

## Purpose

Make multi-session coordination mechanically safe so the user is not required to schedule translator/reviewer work manually. This design adds durable per-unit claims, lease expiry, auditable release/cleanup, deterministic range-conflict handling, and stronger compare-and-swap semantics for shared mutable state.

The repository remains authoritative. Claims are workflow state, not chat/session memory.

## Scope

In scope:

- durable per-unit claims under `.workflow/claims/`;
- translator/reviewer roles;
- session identity, unit identity, base state revision/commit, workflow revision, timestamps, and expiry;
- atomic claim acquisition;
- deterministic overlapping-range rejection;
- release and expired-claim cleanup;
- append-only audit evidence for release/cleanup attempts and completions;
- version-checked deletion;
- stronger filesystem CAS semantics for shared mutable state;
- CLI operations for claim/list/release/cleanup;
- stable JSON output for later `status`/`resume` integration.

Out of scope:

- review-ledger/hash semantics (#9);
- deterministic resume/context selection (#10);
- private-source mode (#11);
- explicit parallel translation policy (#15);
- GitHub API backend (#17);
- migration commands (#16).

## Canonical unit identity

Issue #8 coordinates chapter units already defined by `progress.json`. The canonical unit ID is derived from the validated chapter number, not from a mutable human title or slug:

```text
chapter-000001
chapter-000002
...
```

CLI selectors accept only a positive chapter number (`7`) or an inclusive numeric range (`7-12`) in #8. They are normalized against `progress.json` into canonical unit IDs before any mutation. Missing chapters, reversed ranges, duplicate numbers in progress state, and out-of-book selectors are rejected before claim acquisition starts.

This keeps claim paths stable even when a chapter title/slug changes in a later migration.

## Durable layout

Active claims are one file per unit:

```text
books/<book-slug>/.workflow/claims/<unit-id>.json
```

A unit has one active claim path regardless of role. Therefore Translator and Reviewer cannot concurrently own the same unit.

Claim lifecycle audit evidence is append-only:

```text
books/<book-slug>/.workflow/claim-events/<timestamp>-<event-id>.json
```

Audit events are created through `create_if_absent`; they are never the source of current ownership. Current ownership is defined only by `.workflow/claims/`.

## Claim schema

The existing Workflow v2 `claim` schema is extended with these durable fields:

- `schema_version`;
- `unit_id` matching `chapter-[0-9]{6}`;
- `role`: `translator` or `reviewer`;
- `session_id`;
- `base_revision`: revision token of `progress.json` at acquisition time;
- `base_commit`: Git commit when available, otherwise `null`;
- `workflow_revision`: resolved workflow revision when available, otherwise the most specific recorded requested ref;
- `claimed_at`;
- `expires_at`.

Times are UTC RFC 3339 timestamps. Expiry comparison is performed against an injected clock in domain logic so tests are deterministic.

Unknown explicit schema versions remain fatal. No implicit claim migration is introduced.

A claim cannot be created if the active book has no interpretable workflow revision in metadata; the coordination record must not fabricate provenance.

## Claim acquisition

Single-unit acquisition uses storage `create_if_absent` on the canonical unit claim path. Existing active content yields a conflict; it is never overwritten during acquisition.

Expired claims are still conflicts until an explicit cleanup operation removes them. Acquisition does not silently steal expired claims because cleanup must remain auditable.

### Range acquisition

A requested range is normalized into a sorted unique list of canonical unit IDs before any write.

Algorithm:

1. validate the book/progress state and every requested unit;
2. inspect current claims and reject the first conflicting canonical unit in sorted order before writing when a conflict is already visible;
3. acquire unit claims in canonical unit order with `create_if_absent`;
4. if any acquisition conflicts due to a race, roll back claims already created by this operation using their exact returned revision tokens;
5. report the first conflicting unit in canonical order and create no successful range result unless the entire range is owned.

Rollback failures are surfaced as blocking coordination errors and identify every claim path that could not be rolled back. They are never silently ignored.

Another concurrent requester may briefly observe a partial in-progress range and receive a conflict; it must retry after the first request either completes or rolls back. No requester is told that a range was acquired until all units are durably owned.

## Storage contract changes

Add to `StorageBackend`:

```python
def delete_if_version(path: str, expected_version: str) -> None: ...
```

`delete_if_version` must:

- fail with `StorageNotFound` when the path is absent;
- fail with `StorageVersionConflict` when durable content no longer matches the expected revision;
- remove only the revision that was actually read/acquired.

`WorkflowStateRepository` exposes the corresponding version-checked document deletion helper. Domain code does not bypass schema validation when interpreting a claim.

## Filesystem CAS semantics

Issue #7 introduced SHA-256 revision tokens and stale-write detection but left a small cross-process time-of-check/time-of-use window between the final revision check and `os.replace`.

Issue #8 closes that gap for writers using the filesystem backend.

The filesystem backend uses a short-lived advisory mutex keyed by the resolved storage root plus logical target path while executing `write_if_version` and `delete_if_version`. The mutex implementation uses Python standard-library platform adapters (`fcntl` on POSIX, `msvcrt` on Windows) behind one internal abstraction.

Lock files live outside the logical repository state in the operating-system temporary directory and may persist as empty coordination files. Lock ownership is held by the OS file descriptor, so process termination releases ownership without requiring stale-lock cleanup. Lock files contain no workflow/lease state and are never returned by storage `list()`.

Required properties:

- the mutation critical section covers the version check and final replace/delete;
- two backend writers targeting the same logical path cannot both successfully commit from the same expected revision;
- stale writers fail with `StorageVersionConflict`;
- lock ownership is released on success and exception paths;
- logical path safety remains enforced before any mutation.

Direct out-of-band filesystem edits do not participate in the mutex. They are still detected when they change content before the backend's final revision check, but #8 only guarantees linearization among writers using the storage backend.

The future GitHub backend (#17) will implement the same storage contract using GitHub SHA/conditional mutation semantics rather than filesystem locks.

## Release

Release is ownership-sensitive. The caller supplies a selector and `session_id`.

For each selected unit, domain logic:

1. reads and validates the current claim and its exact revision;
2. verifies the claim belongs to the supplied session;
3. creates an immutable `release_requested` event containing the claim snapshot/revision and reason;
4. removes the active claim using `delete_if_version` with the exact revision that was read;
5. creates an immutable `released` completion event referencing the request event.

A stale session cannot release a newer claim because ownership and version-checked deletion are both required.

If deletion conflicts, the durable `release_requested` event remains as audit evidence of an unsuccessful attempt; no `released` completion event is created. If the final completion-event write fails after deletion, the operation returns an audit-persistence error and the request event remains durable, so the deletion is not invisible.

For a multi-unit release selector, units are processed independently in canonical order. The command returns structured per-unit results and does not claim range-atomic release semantics.

## Expired-claim cleanup

Cleanup is explicit. It does not happen implicitly during acquisition.

For each active claim in canonical order:

1. read and validate the claim and exact revision;
2. compare `expires_at` with the injected current time;
3. skip live claims;
4. create an immutable `cleanup_requested` event containing the claim snapshot/revision, cleanup timestamp, and reason `lease_expired`;
5. delete the claim using `delete_if_version` with the inspected revision;
6. create an immutable `cleaned` completion event referencing the request event.

If the claim changes between inspection and deletion, cleanup records only the request event and reports a conflict rather than deleting the replacement claim.

Cleanup continues across independent units and returns deterministic per-unit results. A conflict on one unit cannot authorize deletion of another unit's live claim.

## Claim event schema

#8 adds a versioned `claim_event` document kind to the schema layer.

Request events include:

- `schema_version`;
- `event_id`;
- `action`: `release_requested` or `cleanup_requested`;
- `unit_id`;
- `claim_revision`;
- `claim`: the validated claim snapshot;
- `occurred_at`;
- `reason`;
- optional `detail`.

Completion events include:

- `schema_version`;
- `event_id`;
- `action`: `released` or `cleaned`;
- `unit_id`;
- `request_event_id`;
- `occurred_at`.

Event filenames contain a sortable UTC timestamp plus unique ID; correctness does not depend on filename parsing. Event IDs are collision-resistant UUID hex values generated by the domain layer.

## Domain component

Add `scripts/workflow_v2/claims.py` containing claim-domain operations independent of CLI and filesystem details.

Responsibilities:

- map validated progress chapters to canonical unit IDs;
- normalize and validate single/range selectors;
- construct and validate claims;
- acquire one or many claims;
- list active claims;
- release owned claims;
- cleanup expired claims;
- create lifecycle audit events;
- return structured domain results/errors.

Dependencies are `WorkflowStateRepository`/`StorageBackend` abstractions plus an injected clock and UUID factory. The module does not call GitHub, shell commands, or argparse.

## CLI

Extend `scripts/book.py` with these exact #8 surfaces:

```text
book.py claim <book> <selector> --role <translator|reviewer> --session-id <id> [--lease-seconds <n>] [--base-commit <sha>] [--json]
book.py claims <book> [--json]
book.py release <book> <selector> --session-id <id> [--json]
book.py cleanup-claims <book> [--json]
```

`selector` is `N` or `N-M` with positive decimal chapter numbers. `--lease-seconds` defaults to `3600` and must be greater than zero. `--base-commit` is optional; when omitted, the helper may resolve the current Git commit when Git is available, otherwise it records `null` without fabricating a value.

Fixed CLI invariants:

- machine-readable JSON output uses stable keys and canonical unit ordering;
- acquisition reports success only after the whole requested selector is acquired;
- acquisition/storage/ownership conflicts return non-zero status;
- `claims` lists active claims in canonical unit order;
- cleanup removes only expired claims;
- release requires matching session ownership;
- CLI delegates coordination semantics to `claims.py` rather than duplicating them.

## Error model

Domain errors distinguish at least:

- invalid selector/unit;
- claim conflict;
- ownership mismatch;
- missing workflow provenance;
- storage version conflict;
- rollback failure;
- invalid claim/event document;
- audit-persistence failure.

Storage exceptions remain specific where their identity matters. CLI converts expected domain/storage conflicts to stable non-zero errors without stack traces.

## Testing strategy

Development is test-first.

### Storage tests

- `delete_if_version` deletes only the expected revision;
- stale version cannot delete replacement content;
- two simultaneous writers using the backend cannot both commit from one expected revision;
- lock ownership is released after ordinary success/failure;
- path safety remains enforced.

### Claim-domain tests

- canonical numeric selectors map to stable unit IDs;
- two sessions cannot acquire the same unit;
- Translator and Reviewer conflict on the same unit;
- expired claim is not silently stolen;
- overlapping ranges are rejected deterministically;
- partial range acquisition rolls back claims created by the failed attempt;
- rollback conflicts are surfaced with remaining paths;
- foreign session cannot release a claim;
- stale release cannot delete a replacement claim;
- cleanup removes expired but not live claims;
- release/cleanup request events precede version-checked deletion;
- completion events exist only after successful deletion;
- injected clock and UUID factory make expiry/event tests deterministic.

### CLI tests

- claim/list/release/cleanup happy paths;
- default and explicit lease duration;
- JSON output stability and canonical ordering;
- non-zero conflicts;
- range acquisition and range release reporting;
- existing extract/validate/build/corpus behavior remains green.

Full suite runs on Python 3.10 and 3.12.

## Acceptance mapping

Issue #8 acceptance criteria map as follows:

1. **Two sessions cannot claim the same unit concurrently** — one canonical unit path + atomic `create_if_absent`, with concurrency tests.
2. **Overlapping ranges are rejected deterministically** — normalized canonical ordering, atomic per-unit acquisition, rollback, and deterministic conflict reporting.
3. **Lost updates fail with conflict** — strengthened filesystem critical section around `write_if_version` plus existing revision tokens.
4. **Expired claims can be cleaned up with an auditable reason** — explicit cleanup request event with `lease_expired`, version-checked deletion, and completion event.
5. **CLI supports claim/list/release/cleanup** — the four `book.py` surfaces above backed by domain operations.

## Dependency and PR strategy

Until #23/#7 is merged, `feature/workflow-v2-claims-cas` is stacked on `feature/workflow-v2-state-core` because #8 directly depends on its schema/storage abstractions.

The #8 implementation PR may initially target `feature/workflow-v2-state-core` so its diff contains only #8 work. After #23 merges into `refactor/workflow-engine-v2`, the branch will be rebased/updated as needed and the PR retargeted to `refactor/workflow-engine-v2`, preserving a clean integration diff.

No merge into the integration branch or `main` is performed without a separate integration decision.

## Self-review resolutions

The design was re-read for scope, ambiguity, and crash/concurrency semantics before implementation planning. Two issues were resolved explicitly:

1. **Audit ordering:** a completion event is never written before version-checked deletion. Durable request events make failed/raced operations auditable without falsely claiming completion.
2. **Unit identity:** claims use progress chapter numbers normalized to fixed canonical IDs, rather than titles/slugs whose textual representation may change.

No TBD/TODO placeholders remain. The design stays within #8 and leaves review, resume, parallel-mode policy, GitHub backend, and migrations to their dedicated issues.
