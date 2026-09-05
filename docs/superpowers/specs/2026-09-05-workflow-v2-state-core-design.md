# Workflow v2 State Core Design

## Context

Issue #7 is the foundation for Workflow v2. The current implementation stores `metadata.json`, `progress.json`, and `source-manifest.json` directly from `scripts/book.py` / `scripts/corpus.py`. Those files already carry `schema_version: 1`, but parsing, validation, persistence, and revision handling are embedded in CLI code. Downstream tasks #8–#12 need a stable state/storage boundary before adding claims, review evidence, resume, and finalize behavior.

## Goals

- Provide versioned parsing/validation contracts for metadata, progress, claims, review ledger, source manifest, and generated state.
- Separate workflow-state operations from storage mechanics.
- Provide storage primitives: `read`, `write_if_version`, `create_if_absent`, and `list`.
- Implement a filesystem backend first.
- Expose opaque revision tokens on reads and successful writes.
- Preserve existing `book.py` behavior and legacy workspaces where practical.
- Keep the implementation standard-library-only and testable without GitHub.

## Non-goals

- Durable claims, leases, overlap detection, or stale-claim cleanup; these belong to #8.
- Review record semantics and stale-review resolution; these belong to #9.
- Resume/status orchestration; this belongs to #10.
- Private-source storage mode; this belongs to #11.
- Workflow migrations; this belongs to #16.
- GitHub API storage; this belongs to #17.

## Package structure

Create `scripts/workflow_v2/` as an internal Python package:

- `schemas.py` — schema identifiers, supported versions, validation, legacy compatibility parsing.
- `storage.py` — backend protocol, immutable read result, and storage errors.
- `filesystem.py` — filesystem implementation of the storage protocol.
- `repository.py` — JSON workflow-state repository that composes schema validation with a storage backend.
- `__init__.py` — stable exports needed by existing CLI modules and later Workflow v2 tasks.

This keeps storage mechanics independent from workflow document semantics and avoids turning `scripts/book.py` into the Workflow v2 domain layer.

## Schema contract

Every authoritative Workflow v2 JSON document has an integer `schema_version`.

Schema kinds:

- `metadata`
- `progress`
- `claim`
- `review_ledger`
- `source_manifest`
- `generated_state`

Version 1 is the only supported version in #7.

Validation is strict about required structural invariants and permissive about unknown additional fields so later tasks can extend records without breaking old readers.

### metadata v1

Required structural fields:

- `schema_version == 1`
- `title`: non-empty string
- `target_language`: non-empty string
- `source_format`: non-empty string
- `source_file`: non-empty basename string
- `chapter_count`: non-negative integer
- `workflow`: object when present

Existing optional fields such as `author`, `source_language`, and `imported_at` remain allowed.

### progress v1

Required structural fields:

- `schema_version == 1`
- `book_slug`: non-empty string
- `chapters`: array

Each chapter must be an object with:

- positive integer `number`
- non-empty string `title`
- non-empty string `slug`
- non-empty relative `source_path`
- non-empty relative `translation_path`
- `status` in `pending|extracted|translated|reviewed`

The schema layer validates record shape. Cross-file lifecycle invariants remain in domain/CLI code until later Workflow v2 tasks move them behind domain operations.

### claim v1

#7 defines only the durable envelope needed by #8:

- `schema_version == 1`
- `unit_id`: non-empty string
- `role`: `translator|reviewer`
- `session_id`: non-empty string
- `base_revision`: non-empty string
- `workflow_revision`: non-empty string
- `claimed_at`: non-empty string
- `expires_at`: non-empty string

Lease timing and overlap semantics are deferred to #8.

### review_ledger v1

#7 defines the top-level durable container only:

- `schema_version == 1`
- `book_slug`: non-empty string
- `records`: array

Record semantics are deferred to #9.

### source_manifest v1

Match the current sealed-corpus structure:

- `schema_version == 1`
- `source_file`: non-empty basename string
- `source_format`: non-empty string
- `source_sha256`: 64-character lowercase hexadecimal SHA-256
- `chapter_count`: non-negative integer
- `extracted`: array

Each extracted entry must contain a positive integer `number`, non-empty `title`, relative `path`, and valid SHA-256.

### generated_state v1

#7 reserves a minimal generated projection envelope without defining finalize semantics:

- `schema_version == 1`
- `book_slug`: non-empty string
- `source_revision`: non-empty string
- `generated_at`: non-empty string
- `data`: object

#12 may extend `data`; generated state is never authoritative over machine state.

## Legacy compatibility

Existing current workspaces already use schema version 1 and are read unchanged.

For older metadata/progress files that omit `schema_version`, the parser may accept them only when they otherwise satisfy the v1 structural shape. It returns a normalized in-memory copy with `schema_version: 1` plus a `legacy=True` flag. Reading legacy state never rewrites files automatically.

Unsupported explicit schema versions fail with `UnsupportedSchemaVersion`; they are not silently upgraded or interpreted as v1.

## Storage protocol

`storage.py` defines:

```python
@dataclass(frozen=True)
class StoredValue:
    content: bytes
    version: str

class StorageBackend(Protocol):
    def read(self, path: str) -> StoredValue: ...
    def write_if_version(self, path: str, content: bytes, expected_version: str) -> str: ...
    def create_if_absent(self, path: str, content: bytes) -> str: ...
    def list(self, prefix: str = "") -> list[str]: ...
```

Errors:

- `StorageNotFound`
- `StorageAlreadyExists`
- `StorageVersionConflict`
- `InvalidStoragePath`

Revision tokens are opaque to callers.

## Filesystem backend

`FilesystemStorage(root)` stores every logical path underneath `root`.

Rules:

- Reject absolute paths, empty path components, `.` / `..`, and paths resolving outside the root.
- `read` returns file bytes and `sha256(content)` as the revision token.
- `create_if_absent` uses exclusive creation and never overwrites an existing file.
- `write_if_version` re-reads the current bytes, compares their SHA-256 with `expected_version`, and rejects a mismatch with `StorageVersionConflict`.
- Successful writes use a sibling temporary file followed by `os.replace` so readers never observe a partially written file.
- Temporary files are cleaned up on failure.
- `list(prefix)` returns sorted relative POSIX paths for regular files only.

#7 provides optimistic version checking and atomic replacement. Cross-process claim/lease coordination and stronger shared-state concurrency policy are completed in #8 rather than hidden in the filesystem backend.

## JSON state repository

`WorkflowStateRepository` composes a `StorageBackend` with schema parsing:

```python
@dataclass(frozen=True)
class LoadedDocument:
    data: dict
    version: str
    legacy: bool

class WorkflowStateRepository:
    def read(self, path: str, schema: SchemaKind) -> LoadedDocument: ...
    def create(self, path: str, schema: SchemaKind, data: Mapping[str, object]) -> str: ...
    def write_if_version(
        self,
        path: str,
        schema: SchemaKind,
        data: Mapping[str, object],
        expected_version: str,
    ) -> str: ...
```

The repository validates before persistence and serializes deterministic UTF-8 JSON with `ensure_ascii=False`, `indent=2`, and one trailing newline.

## Existing CLI integration

`scripts/book.py` continues to expose the same `extract`, `validate`, and `build` CLI.

- New book metadata/progress writes go through `WorkflowStateRepository`.
- `load_book()` reads metadata/progress through the repository and keeps its existing public tuple return shape for compatibility.
- `BookError` remains the CLI-facing error type; workflow/schema/storage errors are translated into concise `BookError` messages at the integration boundary.
- Existing structure/lifecycle checks in `validate_book()` remain in place in #7; schema parsing becomes a prerequisite rather than duplicating all domain validation.

`scripts/corpus.py` reuses the shared source-manifest schema/repository for load/write while retaining corpus sealing/verifying/restoring behavior.

## Error behavior

- Invalid JSON: fail with document path and parse error.
- Missing required field or wrong field type: fail with schema kind and precise field path.
- Unsupported explicit version: fail; do not warn and continue.
- Stale expected revision: fail with `StorageVersionConflict`; do not modify the file.
- Existing target on create: fail with `StorageAlreadyExists`; do not modify the file.
- Invalid logical path: fail before touching the filesystem.

## Testing strategy

Use `unittest`, matching the repository test suite.

New focused tests cover:

1. schema parsing for all six schema kinds;
2. invalid/missing fields and unsupported versions;
3. legacy metadata/progress parsing without on-disk rewrite;
4. filesystem read revision tokens;
5. stale `write_if_version` conflict leaves content unchanged;
6. `create_if_absent` conflict leaves content unchanged;
7. atomic successful replacement produces the new revision;
8. path traversal/absolute path rejection;
9. deterministic sorted `list`;
10. repository JSON serialization and validation-before-write;
11. existing `book.py` smoke tests remain green;
12. existing corpus tests remain green after source-manifest integration.

CI runs the complete suite on Python 3.10 and 3.12 through the existing pull-request workflow.

## Acceptance mapping

- Domain logic testable without GitHub: schema/repository tests use in-process backends/filesystem temp dirs.
- State writes expose revision token: create/write methods return SHA-256 revision tokens and reads include them.
- Explicit schema versions: all six schema kinds enforce version 1.
- Existing CLI compatibility: public `book.py` commands and `load_book()` return shape remain unchanged.
- Schema/conflict tests: dedicated tests cover parsing, legacy behavior, create conflicts, and optimistic stale-write conflicts.
