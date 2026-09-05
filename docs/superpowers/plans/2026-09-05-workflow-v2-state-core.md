# Workflow v2 State Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the versioned Workflow v2 state/storage foundation required by issue #7 without breaking existing book/corpus CLI behavior.

**Architecture:** Add a focused `scripts/workflow_v2/` package with schema validation, a storage protocol, a filesystem backend, and a JSON state repository. Existing CLI modules integrate at their persistence boundaries while keeping current public commands and lifecycle checks unchanged.

**Tech Stack:** Python 3.10+, standard library, `unittest`, GitHub pull-request CI.

**Spec:** `docs/superpowers/specs/2026-09-05-workflow-v2-state-core-design.md`

## Global Constraints

- Standard library only; do not add runtime dependencies.
- Every authoritative Workflow v2 JSON document has explicit integer `schema_version`.
- Unsupported explicit schema versions fail; no silent migration.
- Legacy metadata/progress without `schema_version` may be normalized in memory only when structurally compatible; never auto-rewrite on read.
- Preserve `scripts/book.py extract|validate|build` behavior and `load_book()` return shape.
- #7 must not implement claims/leases, review semantics, resume/finalize, private-source mode, migrations, or GitHub backend behavior owned by later issues.
- Production code follows RED -> GREEN -> REFACTOR; CI must show the RED test commit failing for the intended missing behavior before production implementation lands.

---

### Task 1: Versioned schema contracts

**Files:**
- Create: `tests/test_workflow_v2_schemas.py`
- Create: `scripts/workflow_v2/__init__.py`
- Create: `scripts/workflow_v2/schemas.py`

**Interfaces:**
- Produces: `SchemaKind`, `SchemaError`, `UnsupportedSchemaVersion`, `ParsedDocument`, `parse_document(schema, data, *, allow_legacy=False)`.
- Consumers: `repository.py`, `book.py`, `corpus.py`.

- [ ] **Step 1: Write failing schema tests**

Create tests covering valid metadata/progress/claim/review-ledger/source-manifest/generated-state v1 documents, missing required fields, invalid SHA-256, unsupported version, and legacy metadata/progress normalization with `legacy=True`.

- [ ] **Step 2: Run CI and verify RED**

Open the draft PR after the test commit. Expected failure: import/module errors for `workflow_v2.schemas` or missing schema API. Confirm the failure is caused by the absent production package, not test syntax.

- [ ] **Step 3: Implement minimal schema package**

Implement:

```python
class SchemaKind(str, Enum):
    METADATA = "metadata"
    PROGRESS = "progress"
    CLAIM = "claim"
    REVIEW_LEDGER = "review_ledger"
    SOURCE_MANIFEST = "source_manifest"
    GENERATED_STATE = "generated_state"

@dataclass(frozen=True)
class ParsedDocument:
    data: dict[str, object]
    legacy: bool

def parse_document(
    schema: SchemaKind,
    data: Mapping[str, object],
    *,
    allow_legacy: bool = False,
) -> ParsedDocument:
    ...
```

Use small reusable field validators. Unknown extra fields remain allowed. Explicit `schema_version != 1` raises `UnsupportedSchemaVersion`.

- [ ] **Step 4: Verify GREEN**

CI must pass `tests/test_workflow_v2_schemas.py` on Python 3.10 and 3.12.

- [ ] **Step 5: Commit**

Commit message: `workflow: add versioned state schemas`

---

### Task 2: Storage protocol and filesystem backend

**Files:**
- Create: `tests/test_workflow_v2_storage.py`
- Create: `scripts/workflow_v2/storage.py`
- Create: `scripts/workflow_v2/filesystem.py`
- Modify: `scripts/workflow_v2/__init__.py`

**Interfaces:**
- Produces: `StoredValue(content: bytes, version: str)`, `StorageBackend`, `StorageNotFound`, `StorageAlreadyExists`, `StorageVersionConflict`, `InvalidStoragePath`, `FilesystemStorage(root: Path)`.
- Consumers: `repository.py` and later #8/#17 backends.

- [ ] **Step 1: Write failing storage tests**

Cover read revision = SHA-256 bytes, successful create, duplicate create conflict without mutation, successful conditional write with new revision, stale conditional write conflict without mutation, traversal/absolute path rejection, and sorted recursive list.

- [ ] **Step 2: Verify RED in PR CI**

Expected failure: missing `workflow_v2.storage` / `workflow_v2.filesystem` API.

- [ ] **Step 3: Implement storage contract and filesystem backend**

Required protocol:

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

`FilesystemStorage` must validate logical paths, use SHA-256 revision tokens, use exclusive file creation for `create_if_absent`, and use a same-directory temp file + `os.replace` for successful conditional writes.

- [ ] **Step 4: Verify GREEN**

CI passes storage tests on both Python versions and all schema tests remain green.

- [ ] **Step 5: Commit**

Commit message: `workflow: add filesystem state storage`

---

### Task 3: Schema-aware JSON repository

**Files:**
- Create: `tests/test_workflow_v2_repository.py`
- Create: `scripts/workflow_v2/repository.py`
- Modify: `scripts/workflow_v2/__init__.py`

**Interfaces:**
- Consumes: `StorageBackend`, `SchemaKind`, `parse_document`.
- Produces: `LoadedDocument(data: dict, version: str, legacy: bool)`, `WorkflowStateRepository`.

- [ ] **Step 1: Write failing repository tests**

Tests must show deterministic UTF-8 JSON output, validation occurs before create/write, read returns opaque version + legacy flag, stale expected revision propagates as a storage conflict, and invalid JSON fails with a repository/schema error containing the logical path.

- [ ] **Step 2: Verify RED in CI**

Expected failure: missing repository API.

- [ ] **Step 3: Implement minimal repository**

Required methods:

```python
class WorkflowStateRepository:
    def __init__(self, storage: StorageBackend): ...
    def read(self, path: str, schema: SchemaKind, *, allow_legacy: bool = False) -> LoadedDocument: ...
    def create(self, path: str, schema: SchemaKind, data: Mapping[str, object]) -> str: ...
    def write_if_version(
        self,
        path: str,
        schema: SchemaKind,
        data: Mapping[str, object],
        expected_version: str,
    ) -> str: ...
```

Serialize with `json.dumps(..., ensure_ascii=False, indent=2) + "\n"`.

- [ ] **Step 4: Verify GREEN**

CI passes schema/storage/repository test files on Python 3.10 and 3.12.

- [ ] **Step 5: Commit**

Commit message: `workflow: add schema-aware state repository`

---

### Task 4: Integrate metadata/progress persistence into book CLI

**Files:**
- Modify: `tests/test_book_cli.py`
- Modify: `scripts/book.py`

**Interfaces:**
- Consumes: `FilesystemStorage`, `WorkflowStateRepository`, `SchemaKind`.
- Preserves: `load_book(slug) -> tuple[Path, dict, dict]`, CLI arguments/output semantics.

- [ ] **Step 1: Add failing integration tests**

Add tests that:

- extracted metadata/progress are accepted by shared schema parsing;
- explicit unsupported `schema_version` causes `validate` to fail instead of only warning;
- structurally compatible legacy metadata/progress without `schema_version` still load/validate without being rewritten.

- [ ] **Step 2: Verify RED in CI**

Expected failure: current `validate_book()` warns on unsupported versions rather than rejecting them.

- [ ] **Step 3: Integrate repository at persistence boundary**

Add one helper that creates `WorkflowStateRepository(FilesystemStorage(book_dir))`.

- `extract_command()` creates `metadata.json` and `progress.json` through repository validation.
- `load_book()` reads both documents through the repository with `allow_legacy=True` and translates workflow exceptions to `BookError`.
- Remove schema-version warning branches made obsolete by parser enforcement.
- Keep all existing structural/lifecycle validation and CLI behavior otherwise unchanged.

- [ ] **Step 4: Verify GREEN**

Run full PR CI. All existing `test_book_cli.py` tests and new compatibility/version tests must pass on both Python versions.

- [ ] **Step 5: Commit**

Commit message: `workflow: route book state through repository`

---

### Task 5: Integrate source manifest and finish regression gate

**Files:**
- Modify: `tests/test_corpus_cli.py`
- Modify: `tests/test_corpus_seal.py`
- Modify: `scripts/corpus.py`

**Interfaces:**
- Consumes: shared `SOURCE_MANIFEST` schema and repository.
- Preserves: `corpus.py seal|verify|restore` CLI behavior and existing integrity checks.

- [ ] **Step 1: Add failing source-manifest integration test**

Add a test proving an unsupported explicit source-manifest schema version fails through the shared schema contract and does not get treated as a valid manifest.

- [ ] **Step 2: Verify RED in CI**

Expected failure: current corpus module owns an independent `MANIFEST_SCHEMA_VERSION` validation path.

- [ ] **Step 3: Route manifest load/write through shared repository**

- Replace independent JSON loading/writing with repository calls using `SchemaKind.SOURCE_MANIFEST`.
- Keep hash/content cross-checks in corpus domain code.
- Keep `write_manifest_atomic()` only if another non-state use remains; otherwise remove it.
- Translate shared workflow errors to `book.BookError` at CLI/module boundary.

- [ ] **Step 4: Full verification gate**

PR CI must pass the entire repository suite on Python 3.10 and 3.12.

Also inspect the PR diff for:

- no unrelated workflow/docs/content changes;
- no new dependencies;
- no hidden migration or file rewrites during reads;
- no implementation of #8/#9 semantics;
- every new production behavior covered by a prior RED test commit.

- [ ] **Step 5: Commit**

Commit message: `workflow: unify source manifest state handling`

- [ ] **Step 6: Mark PR ready only after verification**

Update the PR description with acceptance-criteria mapping and exact CI result. Do not merge to `refactor/workflow-engine-v2` until all checks are green and the diff has been reviewed against issue #7.
