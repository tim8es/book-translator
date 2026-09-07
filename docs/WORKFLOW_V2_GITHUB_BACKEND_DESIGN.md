# Workflow v2 GitHub storage backend design

## Status

Approved by standing project authorization after self-review. This design covers issue #17 and targets `refactor/workflow-engine-v2` only.

## Problem

Workflow v2 domain operations already depend on the backend-neutral `StorageBackend` abstraction, but the only concrete durable backend is the local filesystem. ChatGPT Web can operate on GitHub repositories without a local checkout, so Workflow v2 needs a GitHub-backed storage implementation with the same read/list/create/CAS/delete semantics.

The backend must not make GitHub Actions part of execution and must not leak GitHub-specific behavior into claims, review, status, finalize, migration, or literary contracts.

## Goals

1. Implement `StorageBackend` against GitHub repository APIs.
2. Use the GitHub blob SHA as the opaque storage revision returned by `StoredValue.version`.
3. Preserve filesystem-equivalent path safety, create-if-absent, compare-and-swap update/delete, and deterministic listing semantics.
4. Surface concurrent GitHub mutations as existing storage conflict types rather than hiding them behind retries.
5. Keep the GitHub transport injectable so ChatGPT-hosted GitHub capabilities and ordinary REST clients can share the same storage adapter.
6. Prove representative domain operations behave identically with filesystem and GitHub backends.
7. Document read/write capability requirements and fail-closed behavior when permissions or API guarantees are unavailable.

## Non-goals

- No database, queue, daemon, web service, or mandatory GitHub Action.
- No GitHub-specific changes to `docs/TRANSLATION.md` or literary role behavior.
- No automatic backend selection in every existing CLI in this issue. Core domain portability is the requirement; later integration/documentation work may provide environment-specific orchestration entrypoints.
- No automatic retry of mutating GitHub requests.
- No attempt to make several GitHub file commits atomically appear as one repository commit. Workflow v2's existing coordination, journal, rollback, and recovery protocols remain the cross-document safety mechanism.
- No source-book publication or permission broadening.

## Architectural choice

### Chosen: storage adapter over an injectable GitHub API client

Introduce two layers:

1. `github_api.py` defines GitHub file/tree value objects, an error model, a narrow client protocol, and an optional standard-library REST implementation.
2. `github_storage.py` implements the existing `StorageBackend` protocol using that client.

Domain code continues to depend only on `StorageBackend` and `WorkflowStateRepository`.

This is preferred over embedding Contents API calls directly in domain modules because it keeps concurrency and API-specific error classification at the storage boundary. It is preferred over implementing all writes through raw Git object assembly because file-level Contents API CAS already expresses the needed per-path expected-SHA semantics and is simpler to host from ChatGPT Web.

### Rejected: GitHub calls directly in domain code

This would duplicate error handling across claims/finalize/review/migrations and violate backend parity.

### Rejected: raw Git object transaction backend as the primary interface

Trees/commits can create multi-file commits, but they require a branch-head compare-and-swap protocol in addition to per-file identity and add complexity not required by #17. Workflow v2 already owns durable multi-step transaction/recovery semantics above storage.

## Components

### `github_api.py`

Defines transport-facing types:

```python
@dataclass(frozen=True)
class GitHubFile:
    path: str
    blob_sha: str
    content: bytes

@dataclass(frozen=True)
class GitHubTreeEntry:
    path: str
    type: str
    sha: str
    mode: str

@dataclass(frozen=True)
class GitHubTree:
    entries: tuple[GitHubTreeEntry, ...]
    truncated: bool

@dataclass(frozen=True)
class GitHubMutation:
    blob_sha: str | None
    commit_sha: str | None

class GitHubApiError(RuntimeError):
    status: int | None
    message: str

@runtime_checkable
class GitHubApiClient(Protocol):
    def get_file(self, repository: str, path: str, ref: str) -> GitHubFile: ...
    def get_tree(self, repository: str, ref: str) -> GitHubTree: ...
    def create_file(self, repository: str, path: str, content: bytes, branch: str, message: str) -> GitHubMutation: ...
    def update_file(self, repository: str, path: str, content: bytes, expected_blob_sha: str, branch: str, message: str) -> GitHubMutation: ...
    def delete_file(self, repository: str, path: str, expected_blob_sha: str, branch: str, message: str) -> GitHubMutation: ...
```

The protocol is expressed in repository/file operations, not ChatGPT connector method names. A host adapter and a token-backed REST client can both implement it.

### `GitHubRestClient`

A concrete standard-library implementation supports environments that can make direct GitHub HTTPS requests.

Constructor:

```python
GitHubRestClient(
    token: str | None,
    *,
    base_url: str = "https://api.github.com",
    api_version: str = "2026-03-10",
    opener=None,
)
```

Rules:

- caller supplies credentials; the client does not read environment variables itself;
- requests use GitHub JSON media type and `X-GitHub-Api-Version: 2026-03-10`;
- URL path segments are safely quoted;
- write bodies encode bytes as Base64;
- file reads resolve the file blob SHA then fetch the Git blob bytes so reads are binary-safe and do not rely on small Contents inline payload limits;
- directories, symlinks, submodules, and other non-file entries are rejected as ordinary files;
- recursive tree responses expose `truncated` and are rejected by `GitHubStorage` when incomplete;
- HTTP/JSON failures become `GitHubApiError` with status and concise context;
- no mutation retry is performed.

The REST client is tested with injected fake HTTP handling. CI never depends on live GitHub networking or credentials.

### `GitHubStorage`

Constructor:

```python
GitHubStorage(
    client: GitHubApiClient,
    *,
    repository: str,
    branch: str,
    root_prefix: str = "",
    commit_prefix: str = "workflow-v2",
)
```

`repository` is `owner/name`. Mutations always target a branch, not a detached commit SHA. `root_prefix` maps a logical backend root to a repository directory such as `books/my-book`.

## Logical path safety

GitHubStorage applies the same logical path rules as filesystem storage before any API call:

- path must be a string;
- empty path is allowed only for `list("")`;
- no absolute path;
- no backslash;
- no empty, `.` or `..` segments.

`root_prefix` is validated once using equivalent relative-POSIX rules. Repository paths are formed only from validated components; callers cannot escape the configured backend root.

`list(prefix)` returns logical paths relative to `root_prefix`, not repository-global paths.

## Revision model

`StoredValue.version` is the GitHub **blob SHA** for the exact file bytes.

Rationale:

- the abstraction promises a per-value revision, not a repository commit;
- Contents update/delete operations accept the current file/blob SHA as their CAS token;
- a branch may advance for unrelated files without invalidating a value revision;
- existing domain code treats revisions as opaque strings.

Commit SHA remains secondary audit metadata inside transport results and never replaces the storage version.

## Read semantics

`read(path)`:

1. validate/map the logical path;
2. call `client.get_file(repository, repo_path, branch)`;
3. map API 404 for path lookup to `StorageNotFound`;
4. return exact bytes and `blob_sha` as `StoredValue.version`;
5. map authorization, transport, malformed response, or invalid entry errors to `StorageError` with concise capability context.

A private repository may intentionally return 404 for insufficient permission. For storage compatibility a path lookup 404 maps to `StorageNotFound`; capability documentation warns that callers needing permission diagnostics must establish repository access separately.

## List semantics

`list(prefix)`:

1. validate prefix;
2. obtain the recursive Git tree for `branch`;
3. fail closed with `StorageError` if the response is truncated;
4. select ordinary blob entries beneath `root_prefix`;
5. retain paths matching the requested logical prefix: exact file returns itself, directory prefix returns descendants, missing prefix returns `[]`;
6. strip `root_prefix` and return sorted POSIX paths.

Recursive Trees API is used instead of Contents directory enumeration because bounded directory responses could make large workspaces silently incomplete.

## Create semantics

`create_if_absent(path, content)`:

1. validate path and require `bytes` content;
2. invoke create-file without an expected SHA;
3. classify a proven existing-path failure as `StorageAlreadyExists`;
4. do not retry a failed mutation;
5. read the path back from the branch;
6. require read-back bytes to equal intended content;
7. return the read-back blob SHA.

Read-after-write is mandatory even when a transport returns a blob SHA. This keeps behavior compatible with hosted adapters that may return only commit identity and detects a concurrent overwrite after our mutation.

If read-back contains different bytes, return `StorageVersionConflict`: our create may have committed, but another writer changed the path before confirmation, so reporting success is unsafe.

## Update semantics

`write_if_version(path, content, expected_version)`:

1. validate inputs;
2. pre-read current file;
3. missing path -> `StorageNotFound`;
4. current SHA != expected -> `StorageVersionConflict` without mutation;
5. send one update with the expected blob SHA;
6. on GitHub 409/422, re-read only to classify: missing -> `StorageNotFound`; changed SHA -> `StorageVersionConflict`; unchanged expected SHA -> generic `StorageError`;
7. after apparent success, read back;
8. exact intended bytes -> return current blob SHA;
9. different bytes -> `StorageVersionConflict`.

There is no blind retry. The domain operation must re-read/replan according to its existing conflict/recovery protocol.

## Delete semantics

`delete_if_version(path, expected_version)` mirrors update:

1. pre-read and compare blob SHA;
2. send one delete with expected SHA;
3. classify 409/422 with a fresh read;
4. after apparent success, verify the path is absent;
5. if a file exists afterward, return `StorageVersionConflict` because another writer won after deletion.

A missing path before deletion remains `StorageNotFound`.

## Error classification

| GitHub/API condition | Storage exception |
| --- | --- |
| Missing path lookup | `StorageNotFound` |
| Create proves path already exists | `StorageAlreadyExists` |
| Current/read-back SHA or bytes prove race | `StorageVersionConflict` |
| Unsafe logical path | `InvalidStoragePath` |
| 401/403, malformed response, truncated tree, network/transport failure | `StorageError` |
| 409/422 without evidence of changed version | `StorageError` |

Permission denial or validation errors are never called concurrency unless a fresh read proves the expected version changed.

## Commit semantics

Each mutating storage operation produces at most one GitHub file commit. Commit messages are deterministic and bounded:

- `workflow-v2: create <repo-path>`
- `workflow-v2: update <repo-path>`
- `workflow-v2: delete <repo-path>`

The backend does not use commit history as authoritative workflow state. Blob identity and durable files remain authoritative; Git history is secondary audit evidence.

## Domain behavior

No GitHub-specific changes are expected in `claims.py`, `reviews.py`, `status.py`, `finalize.py`, `migrations.py`, or `repository.py`.

Representative parity tests instantiate those components with `WorkflowStateRepository(GitHubStorage(fake_client, ...))` and exercise coordination/CAS behavior. If a domain module needs GitHub-specific branching, the design has failed and should be corrected at the backend boundary instead.

## Hosted ChatGPT execution

The storage adapter itself does not import ChatGPT connector APIs. A host adapter may implement `GitHubApiClient` using the connected GitHub capability and pass it to `GitHubStorage`.

This keeps Workflow v2 portable across ChatGPT Web connected GitHub execution, an ordinary Python process with direct GitHub REST access, and deterministic in-memory tests. No GitHub Action is needed for domain execution.

## Permissions and capability failure

Minimum remote capabilities:

- repository/Contents read for `read` and `list`;
- repository/Contents write for create/update/delete;
- Git tree/blob read for complete listing and binary-safe reads.

A read-only connection can still perform status-like reads but mutations fail with `StorageError` and a concise capability message. The backend never requests broader permissions, stores tokens in workflow state, or logs credentials.

## Tests

### Reusable backend contract

Exercise identical assertions against FilesystemStorage and GitHubStorage:

- create/read exact bytes and revision;
- duplicate create rejected without overwrite;
- current-version update succeeds;
- stale update preserves winner;
- current-version delete succeeds;
- stale delete preserves current file;
- missing read/update/delete semantics;
- nested deterministic listing, exact-file prefix, missing prefix;
- unsafe paths rejected before backend mutation.

### GitHub-specific storage tests

- blob SHA is returned as version;
- root-prefix mapping is exact;
- truncated recursive tree fails closed;
- authorization failure maps to `StorageError`;
- deterministic mutation messages;
- 409/422 is re-read and classified from durable state;
- apparent success followed by concurrent overwrite becomes conflict;
- mutations are never automatically retried.

### REST client tests

Using fake HTTP responses only:

- headers/version/auth behavior;
- path quoting;
- file metadata + blob Base64 decode;
- tree parsing/truncation;
- Base64 mutation payloads and branch/expected-SHA fields;
- success parsing;
- 401/403/404/409/422 and malformed JSON error mapping;
- no network dependency.

### Domain parity

Run representative existing operations against GitHubStorage with a fake client:

- claim acquire/conflict/release;
- book coordination lock lifecycle;
- status read-only resolution;
- one CAS-backed state transition/finalize admission path.

No domain production changes are expected.

### #18 reliability extension

Inject races/failures around the GitHub client boundary:

- stale writer between pre-read and update;
- concurrent overwrite between successful mutation and read-back;
- transient transport error does not cause duplicate commit retry;
- truncated list blocks operation rather than accepting partial state;
- claim conflict is recoverable by fresh read.

Production changes are made only when a reliability test demonstrates a real defect.

## Documentation

Add GitHub backend execution/capability guidance to orchestration/setup documentation only. The literary translation contract stays backend-agnostic. Documentation states GitHub Actions remain optional CI and are not required for read/write workflow operations.

## Release and audit gates

Before merge to `refactor/workflow-engine-v2`:

1. exact feature head passes full Python 3.10/3.12 suite;
2. backend contract passes for filesystem and GitHub implementations;
3. REST client tests are deterministic and network-free;
4. domain parity tests require no GitHub-specific domain branches;
5. #18 failure-injection coverage is green;
6. changed files remain limited to backend/runtime tests and orchestration/setup capability docs;
7. PR has no unresolved reviews/threads;
8. integration branch has not moved or feature is updated safely;
9. `main` remains unchanged;
10. merge is expected-head guarded into integration only and feature branch is preserved.
