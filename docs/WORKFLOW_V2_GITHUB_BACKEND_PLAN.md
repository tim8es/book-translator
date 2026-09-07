# Workflow v2 GitHub Storage Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub API-backed `StorageBackend` that preserves Workflow v2 filesystem semantics and lets domain operations run against GitHub without a local checkout or mandatory GitHub Actions.

**Architecture:** `github_api.py` owns transport-facing value types, errors, a narrow client protocol, and a standard-library REST client. `github_storage.py` adapts that client to the existing storage protocol using blob SHA revisions, strict path mapping, read-after-write verification, no mutation retries, and fail-closed error classification. Existing domain modules remain backend-neutral and are exercised by parity tests.

**Tech Stack:** Python 3.10+, standard library (`urllib`, `json`, `base64`), existing `StorageBackend` / `WorkflowStateRepository`, `unittest`, GitHub REST API version `2026-03-10`.

**Spec:** `docs/WORKFLOW_V2_GITHUB_BACKEND_DESIGN.md`

## Global Constraints

- Develop only on `feature/workflow-v2-github-backend`, based on integration commit `74cdfa9f5911bbff733907579da2a4b930c4090d`.
- PR targets `refactor/workflow-engine-v2`; never merge to `main` without explicit final user authorization.
- GitHub Actions are CI only; runtime behavior must not require an Action.
- No database, queue, daemon, mandatory SDK, or new external service.
- Domain code depends only on `StorageBackend`; no GitHub-specific branches in claims/reviews/status/finalize/migrations/repository.
- Blob SHA is an opaque storage revision; do not assume SHA length or algorithm.
- No blind retry of any mutating GitHub request.
- Recursive tree truncation is a hard failure, never partial success.
- Read-after-write verification is mandatory for create/update/delete.
- Literary contracts remain backend-agnostic.
- Every production slice follows test-only RED -> focused GREEN -> full-suite GREEN.

---

### Task 1: Reusable storage contract and GitHub storage core

**Files:**
- Create: `tests/storage_contract.py`
- Create: `tests/github_fake.py`
- Create: `tests/test_workflow_v2_storage_contract.py`
- Create: `tests/test_workflow_v2_github_storage.py`
- Create: `scripts/workflow_v2/github_api.py`
- Create: `scripts/workflow_v2/github_storage.py`
- Modify: `scripts/workflow_v2/__init__.py`

**Interfaces:**

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
    def __init__(self, message: str, *, status: int | None = None): ...

@runtime_checkable
class GitHubApiClient(Protocol):
    def get_file(self, repository: str, path: str, ref: str) -> GitHubFile: ...
    def get_tree(self, repository: str, ref: str) -> GitHubTree: ...
    def create_file(self, repository: str, path: str, content: bytes, branch: str, message: str) -> GitHubMutation: ...
    def update_file(self, repository: str, path: str, content: bytes, expected_blob_sha: str, branch: str, message: str) -> GitHubMutation: ...
    def delete_file(self, repository: str, path: str, expected_blob_sha: str, branch: str, message: str) -> GitHubMutation: ...

class GitHubStorage:
    def __init__(self, client: GitHubApiClient, *, repository: str, branch: str, root_prefix: str = "", commit_prefix: str = "workflow-v2"): ...
```

- [ ] **Step 1 — RED contract helper:** create `tests/storage_contract.py` with `exercise_backend_contract(testcase, factory)`. It must assert exact binary create/read, duplicate create, current/stale update, current/stale delete, missing read/update/delete, nested sorted list, exact-file prefix, missing prefix, and unsafe paths. The stale cases must prove the winner bytes remain unchanged.

```python
def exercise_backend_contract(testcase, factory):
    storage = factory()
    version = storage.create_if_absent("nested/state.bin", b"alpha\x00beta")
    loaded = storage.read("nested/state.bin")
    testcase.assertEqual(loaded.content, b"alpha\x00beta")
    testcase.assertEqual(loaded.version, version)
```

- [ ] **Step 2 — RED filesystem contract class:** create `tests/test_workflow_v2_storage_contract.py` that runs the new contract against a fresh `FilesystemStorage` root. Do not modify existing filesystem storage tests.

- [ ] **Step 3 — RED deterministic GitHub fake:** create `tests/github_fake.py` with `FakeGitHubApiClient`. It stores repository paths as exact bytes, derives deterministic opaque blob IDs from bytes, exposes a recursive tree, records mutation calls/messages, and supports one-shot before/after/error hooks used by later tasks.

- [ ] **Step 4 — RED GitHub contract class:** create `tests/test_workflow_v2_github_storage.py`. Import the planned public GitHub API/storage surface through a `require_api()` helper so pre-implementation failures identify only the missing backend. Run the same `exercise_backend_contract` using `root_prefix="books/sample"`, and assert fake-client paths are repository-relative under that prefix.

- [ ] **Step 5 — Run focused RED:** `python -m unittest tests.test_workflow_v2_storage_contract tests.test_workflow_v2_github_storage -v`. Expected: filesystem contract green; GitHub cases fail because `GitHubStorage`/GitHub API types are not implemented.

- [ ] **Step 6 — Implement `github_api.py` transport data/protocol:** add the dataclasses, runtime-checkable protocol, and `GitHubApiError`. Require non-empty error messages; status must be `int` or `None`. Do not implement HTTP yet.

- [ ] **Step 7 — Implement GitHubStorage construction/path mapping:** validate `repository` as exactly two non-empty `owner/name` components, non-empty `branch`, non-empty `commit_prefix`, and safe relative POSIX `root_prefix`. Implement path validation matching filesystem storage: empty only for `list("")`, no absolute paths, backslashes, empty segments, `.` or `..`.

- [ ] **Step 8 — Implement read/list:** `read` maps client 404 to `StorageNotFound`, returns exact bytes and blob SHA, and maps all other API errors to `StorageError`. `list` calls recursive tree, rejects `truncated=True`, accepts only ordinary `blob` entries with mode `100644`/`100755`, strips `root_prefix`, applies exact-file-or-descendant prefix semantics, and sorts results.

- [ ] **Step 9 — Implement create/update/delete happy paths:** update/delete pre-read and compare exact opaque blob SHA before mutation. Each operation performs exactly one client mutation with `workflow-v2: <verb> <repo-path>`, then verifies durable state by fresh read. Create/update return read-back blob SHA; delete requires the read-back to be missing. Do not trust mutation-return SHA as final proof.

- [ ] **Step 10 — Export public types:** update `scripts/workflow_v2/__init__.py` to export `GitHubApiClient`, `GitHubApiError`, `GitHubFile`, `GitHubTree`, `GitHubTreeEntry`, `GitHubMutation`, and `GitHubStorage`.

- [ ] **Step 11 — Focused GREEN:** `python -m unittest tests.test_workflow_v2_storage_contract tests.test_workflow_v2_storage tests.test_workflow_v2_github_storage -v`.

- [ ] **Step 12 — Full GREEN:** run `python -m unittest discover -s tests -v` through the Python 3.10/3.12 CI matrix.

- [ ] **Step 13 — Commit boundary:** preserve test-only RED separately from production. Suggested production commit: `feat: add GitHub storage backend core`.

---

### Task 2: GitHub race and error classification

**Files:**
- Modify: `tests/test_workflow_v2_github_storage.py`
- Modify: `scripts/workflow_v2/github_storage.py`

**Interfaces:** consumes Task 1 `GitHubStorage` and `GitHubApiError(status=...)`.

- [ ] **Step 1 — RED race tests:** use the deterministic fake hooks to assert:
  - stale expected SHA is rejected before any mutation call;
  - create 409/422 followed by an existing path => `StorageAlreadyExists`;
  - update 409/422 followed by changed SHA => `StorageVersionConflict`;
  - update 409/422 while expected SHA remains current => `StorageError`;
  - delete 409/422 followed by changed SHA => `StorageVersionConflict`;
  - successful create/update followed by immediate concurrent overwrite => `StorageVersionConflict`;
  - successful delete followed by recreation => `StorageVersionConflict`;
  - 401/403 => concise `StorageError` describing unavailable GitHub contents capability;
  - mutation call count remains one; no automatic retry occurs.

- [ ] **Step 2 — Run focused RED:** `python -m unittest tests.test_workflow_v2_github_storage -v`. Expected: only the newly specified race/classification behavior fails.

- [ ] **Step 3 — Implement mutation-failure classification:** for client 409/422 perform one fresh read. Create classifies a now-existing path as `StorageAlreadyExists`. Update/delete classify missing/changed versions from durable state; if expected state is still current, raise generic `StorageError` rather than fabricating a race. Preserve the client exception as cause.

- [ ] **Step 4 — Implement post-mutation race verification:** create/update compare exact intended bytes; delete verifies absence. Any different winner state becomes `StorageVersionConflict`.

- [ ] **Step 5 — GREEN focused + full Python 3.10/3.12 matrix.**

- [ ] **Step 6 — Commit:** `feat: classify GitHub storage races safely`.

---

### Task 3: Standard-library GitHub REST client

**Files:**
- Create: `tests/test_workflow_v2_github_api.py`
- Modify: `scripts/workflow_v2/github_api.py`
- Modify: `scripts/workflow_v2/__init__.py`

**Interfaces:**

```python
class GitHubRestClient:
    def __init__(self, token: str | None, *, base_url: str = "https://api.github.com", api_version: str = "2026-03-10", opener=None): ...
```

- [ ] **Step 1 — RED HTTP harness:** create a fake opener/response in `tests/test_workflow_v2_github_api.py` that records every `urllib.request.Request` method, URL, headers, and body and returns queued response bytes without network access.

- [ ] **Step 2 — RED read tests:** require `GET /repos/{owner}/{repo}/contents/{quoted-path}?ref={quoted-ref}`, validate `type == "file"` and non-empty `sha`, then require `GET /repos/{owner}/{repo}/git/blobs/{sha}`. Blob response must declare `encoding == "base64"`; strict Base64 decoding returns `GitHubFile` with exact bytes and the contents SHA. Non-file/malformed/invalid-base64 responses become `GitHubApiError`.

- [ ] **Step 3 — RED tree tests:** require `GET /repos/{owner}/{repo}/git/trees/{quoted-ref}?recursive=1`, exact entry parsing, and preservation of the response `truncated` boolean.

- [ ] **Step 4 — RED mutation tests:** create/update bodies contain Base64 `content`, `branch`, and `message`; update additionally contains `sha`. Delete contains `branch`, `message`, and `sha` but no content. Parse optional content SHA and commit SHA into `GitHubMutation`; delete accepts the documented success response without requiring content metadata.

- [ ] **Step 5 — RED header/error tests:** every request has `Accept: application/vnd.github+json` and `X-GitHub-Api-Version: 2026-03-10`; `Authorization: Bearer <token>` exists only with a supplied token. HTTP 401/403/404/409/422, URL/transport failures, invalid UTF-8, and malformed JSON become `GitHubApiError`; token text never appears in messages.

- [ ] **Step 6 — Run focused RED:** `python -m unittest tests.test_workflow_v2_github_api -v`. Expected: missing `GitHubRestClient`/HTTP methods only.

- [ ] **Step 7 — Implement `_request_json`:** use the injected opener or `urllib.request.build_opener()`, deterministic JSON request bodies, UTF-8 JSON response decoding, `urllib.error.HTTPError`/`URLError` plus timeout/OSError handling, no retries, concise sanitized `GitHubApiError`.

- [ ] **Step 8 — Implement client methods:** quote repository/path/ref pieces safely with `urllib.parse.quote`; validate JSON shapes; strict Base64 decode; construct Task 1 values. Do not read tokens from environment variables.

- [ ] **Step 9 — Export `GitHubRestClient`; GREEN focused + full matrix.**

- [ ] **Step 10 — Commit:** `feat: add GitHub REST storage transport`.

---

### Task 4: Domain parity on GitHubStorage

**Files:**
- Create: `tests/test_workflow_v2_github_backend_domain.py`
- Reuse: `tests/github_fake.py`
- Production: none unless a test proves a backend-boundary defect.

- [ ] **Step 1 — Build canonical remote workspace:** use `WorkflowStateRepository(GitHubStorage(...))` to create schema-valid metadata/progress/ledger/source-manifest state under `books/sample`; do not seed JSON by bypassing repository validation.

- [ ] **Step 2 — Claim parity:** acquire a translator claim, prove a second session conflicts, release by owner, and verify claim/audit durable state through GitHubStorage.

- [ ] **Step 3 — Coordination parity:** acquire a coordination lease, prove concurrent live lease conflict, release, and reacquire.

- [ ] **Step 4 — Status parity:** resolve status/resume for an extracted unit and assert the fake client recorded zero mutation calls during status resolution.

- [ ] **Step 5 — CAS parity:** perform one repository/domain write, retain its old revision, commit a competing winner, then prove stale write fails and winner bytes remain authoritative.

- [ ] **Step 6 — Run:** `python -m unittest tests.test_workflow_v2_github_backend_domain -v`. Immediate GREEN is valid test-only evidence. A genuine failure triggers `systematic-debugging`; fix only the backend boundary unless evidence proves an existing domain abstraction bug.

- [ ] **Step 7 — Full matrix; commit parity tests separately from any production fix.**

---

### Task 5: Capability and orchestration documentation

**Files:**
- Modify: `tests/test_agent_contract.py`
- Modify: `docs/ORCHESTRATION.md`
- Modify: `docs/AGENT_SETUP.md`

- [ ] **Step 1 — RED contract tests:** assert orchestration/setup docs explicitly state GitHub API storage is an allowed orchestrator execution substrate, core durable read/write execution does not require GitHub Actions, read-only access requires repository contents/tree/blob reads, mutations require contents write, CAS conflict requires re-read/replan instead of blind retry, credentials are never persisted in book state, and `docs/TRANSLATION.md` contains no GitHub client/API execution instructions.

- [ ] **Step 2 — Run focused RED:** `python -m unittest tests.test_agent_contract -v`.

- [ ] **Step 3 — Update orchestration docs:** add a compact GitHub-backed execution section near execution modes/single-writer rules. Keep transport mechanics out of Translator/Reviewer instructions.

- [ ] **Step 4 — Update setup docs:** document repository/branch/root scoping, read vs write capabilities, explicit credential injection/non-persistence, fail-closed permission behavior, and that Actions remain optional CI only.

- [ ] **Step 5 — GREEN agent contract + full matrix.**

- [ ] **Step 6 — Commit:** `docs: document GitHub-backed Workflow v2 execution`.

---

### Task 6: #18 GitHub backend reliability

**Files:**
- Create: `tests/test_workflow_v2_github_backend_reliability.py`
- Reuse: `tests/github_fake.py`
- Production: only when failure injection demonstrates a real defect.

- [ ] **Step 1 — Failure injection:** cover six independent cases:
  1. competing writer changes target after pre-read but before update -> conflict, winner preserved;
  2. competing writer overwrites immediately after successful update -> read-back conflict, winner preserved;
  3. transport failure during mutation -> exactly one mutation attempt, no hidden retry;
  4. truncated recursive tree -> list and domain discovery fail closed rather than accepting partial state;
  5. two claim create attempts race -> one owner wins, loser gets recoverable conflict behavior;
  6. a fresh read after conflict observes the winner and permits normal caller recovery.

- [ ] **Step 2 — Run focused:** `python -m unittest tests.test_workflow_v2_github_backend_reliability -v`. Immediate GREEN is test-only evidence. For real RED, preserve the failing run before a minimum fix.

- [ ] **Step 3 — Full matrix GREEN.**

- [ ] **Step 4 — Commit reliability tests separately from any production fix.**

---

### Task 7: Final verification and integration audit

**Files:** no intended production changes.

- [ ] **Step 1 — Exact-head full suite:** `python -m unittest discover -s tests -v` succeeds on Python 3.10 and 3.12 for the exact final feature head; record run id and exact test count.

- [ ] **Step 2 — Acceptance audit:** map evidence to core coordination through GitHub without Actions, recoverable CAS conflicts, common filesystem/GitHub backend contract, concise capability failures, and absence of GitHub-specific literary/domain branching.

- [ ] **Step 3 — Scope audit:** changed files are limited to design/plan, `github_api.py`, `github_storage.py`, package exports, fake/contract/backend/parity/reliability tests, `docs/ORCHESTRATION.md`, `docs/AGENT_SETUP.md`, and agent contract tests. Any domain production change requires explicit test-proven justification in the PR body.

- [ ] **Step 4 — PR audit:** base `refactor/workflow-engine-v2`; behind by 0 or safely updated; merge base matches expected integration ancestry; no unresolved comments/reviews/threads; `main` unchanged.

- [ ] **Step 5 — PR evidence:** include RED/GREEN run ids, exact final test count, backend contract/parity/reliability mapping, base/head SHAs, and explicit statement that GitHub Actions are not a runtime dependency.

- [ ] **Step 6 — Ready + merge:** mark Ready only after all guards pass. Merge with `expected_head_sha` into `refactor/workflow-engine-v2` only. Preserve `feature/workflow-v2-github-backend`. Never merge `main`.
