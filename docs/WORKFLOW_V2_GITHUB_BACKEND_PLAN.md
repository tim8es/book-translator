# Workflow v2 GitHub Storage Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub API-backed `StorageBackend` that preserves Workflow v2 filesystem semantics and lets domain operations run against GitHub without a local checkout or mandatory GitHub Actions.

**Architecture:** `github_api.py` owns transport-facing value types, errors, a narrow client protocol, and a standard-library REST client. `github_storage.py` adapts that client to the existing backend-neutral storage protocol using blob SHA revisions, strict path mapping, read-after-write verification, no mutation retries, and fail-closed error classification. Existing domain modules remain unchanged and are exercised through parity tests.

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
- Literary contracts (`docs/TRANSLATION.md`) remain backend-agnostic.
- Every production slice follows test-only RED -> focused GREEN -> full-suite GREEN.

---

### Task 1: Reusable storage contract and GitHub storage core

**Files:**
- Create: `tests/storage_contract.py`
- Create: `tests/test_workflow_v2_github_storage.py`
- Create: `scripts/workflow_v2/github_api.py`
- Create: `scripts/workflow_v2/github_storage.py`
- Modify: `scripts/workflow_v2/__init__.py`

**Interfaces:**
- Consumes: existing `StorageBackend`, `StoredValue`, `StorageNotFound`, `StorageAlreadyExists`, `StorageVersionConflict`, `InvalidStoragePath`, `StorageError`.
- Produces:

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

- [ ] **Step 1 — RED contract helper:** create `tests/storage_contract.py` with a mixin/function set that checks create/read exact bytes, duplicate create, current/stale update, current/stale delete, missing read/update/delete, nested sorted list, exact-file prefix, missing prefix, and unsafe paths. Reuse it from filesystem tests if this can be done without changing their semantics; otherwise instantiate it independently for both backends.

Core assertion shape:

```python
def exercise_backend_contract(testcase, factory):
    storage = factory()
    version = storage.create_if_absent("nested/state.bin", b"alpha\x00beta")
    loaded = storage.read("nested/state.bin")
    testcase.assertEqual(loaded.content, b"alpha\x00beta")
    testcase.assertEqual(loaded.version, version)
```

The helper must also prove a stale writer never overwrites the winner.

- [ ] **Step 2 — RED GitHub fake:** in `tests/test_workflow_v2_github_storage.py`, define an in-memory `FakeGitHubApiClient` whose file values use deterministic synthetic blob identities derived from bytes and whose tree reflects current files. Add contract tests importing `GitHubStorage`; before production exists, `require_api()` must fail only because `github_api`/`github_storage` are absent.

- [ ] **Step 3 — Run focused RED:** `python -m unittest tests.test_workflow_v2_github_storage -v`. Expected: failures identify the missing GitHub storage/API surface, while existing storage tests remain green.

- [ ] **Step 4 — Implement transport data/protocol:** add `github_api.py` value objects/protocol/error only. Validate constructor arguments minimally: error message non-empty; status integer or null. No HTTP implementation yet.

- [ ] **Step 5 — Implement path mapping:** in `github_storage.py`, validate repository as `owner/name`, non-empty branch/commit prefix, and safe `root_prefix`. Implement `_logical_path(path, allow_empty=False)` and `_repo_path(logical)` using POSIX components only. Reject unsafe values before client calls with `InvalidStoragePath`.

- [ ] **Step 6 — Implement read/list:** map `GitHubApiError(status=404)` from `get_file` to `StorageNotFound`; other client errors to concise `StorageError`. For list, call `get_tree`, reject `truncated=True`, include only `type == "blob"` with regular-file modes `100644` or `100755`, strip root prefix, apply exact-file-or-descendant prefix semantics, and return sorted logical paths.

- [ ] **Step 7 — Implement create/update/delete happy paths:** pre-read for update/delete, compare exact opaque blob SHA, make one client mutation with deterministic message, then verify durable state by fresh read. Create reads back and returns its blob SHA; update returns read-back SHA; delete requires read-back 404. Do not use `GitHubMutation.blob_sha` as proof of final state.

- [ ] **Step 8 — Export public backend types:** update `scripts/workflow_v2/__init__.py` to export `GitHubApiClient`, `GitHubApiError`, `GitHubFile`, `GitHubTree`, `GitHubTreeEntry`, `GitHubMutation`, and `GitHubStorage` without introducing transport imports elsewhere.

- [ ] **Step 9 — Focused GREEN:** run `python -m unittest tests.test_workflow_v2_storage tests.test_workflow_v2_github_storage -v`. Expected: identical contract behavior for both backends.

- [ ] **Step 10 — Full GREEN:** run `python -m unittest discover -s tests -v` on Python 3.10/3.12 CI.

- [ ] **Step 11 — Commit boundary:** commits preserve separate test-only RED evidence and minimum production GREEN. Suggested production commit: `feat: add GitHub storage backend core`.

---

### Task 2: GitHub race and error classification

**Files:**
- Modify: `tests/test_workflow_v2_github_storage.py`
- Modify: `scripts/workflow_v2/github_storage.py`

**Interfaces:**
- Consumes: Task 1 `GitHubStorage` and `GitHubApiError(status=...)`.
- Produces: deterministic mapping from GitHub mutation races/capability failures to existing storage exceptions.

- [ ] **Step 1 — RED race tests:** extend fake client with one-shot hooks able to mutate state immediately before a mutation, immediately after a mutation, or raise `GitHubApiError` with a selected status. Add tests for:
  - stale expected SHA rejected before client mutation;
  - create 409/422 followed by existing file => `StorageAlreadyExists`;
  - update 409/422 followed by changed SHA => `StorageVersionConflict`;
  - update 409/422 while expected SHA is still current => `StorageError`;
  - delete 409/422 followed by changed SHA => `StorageVersionConflict`;
  - successful create/update followed by concurrent overwrite => `StorageVersionConflict`;
  - successful delete followed by recreation => `StorageVersionConflict`;
  - 401/403 => `StorageError` mentioning unavailable GitHub contents capability;
  - client call count proves no automatic mutation retry.

- [ ] **Step 2 — Run focused RED:** expect only the newly specified classification/race cases to fail.

- [ ] **Step 3 — Implement `_classify_mutation_failure`:** for mutation 409/422, perform at most one fresh read to classify durable state. Never convert unchanged expected state into a conflict without proof. Preserve the original API error as `__cause__`.

- [ ] **Step 4 — Implement post-mutation verification:** use exact bytes for create/update and absence for delete. A competing read-back state becomes `StorageVersionConflict`; never silently return the intermediate mutation result.

- [ ] **Step 5 — GREEN focused + full matrix.**

- [ ] **Step 6 — Commit:** `feat: classify GitHub storage races safely`.

---

### Task 3: Standard-library GitHub REST client

**Files:**
- Create: `tests/test_workflow_v2_github_api.py`
- Modify: `scripts/workflow_v2/github_api.py`

**Interfaces:**
- Consumes: Task 1 data classes/protocol/error.
- Produces:

```python
class GitHubRestClient:
    def __init__(self, token: str | None, *, base_url: str = "https://api.github.com", api_version: str = "2026-03-10", opener=None): ...
```

- [ ] **Step 1 — RED HTTP harness:** create a fake opener/response that records `urllib.request.Request` method, URL, headers, and body and returns queued bytes/status without network access.

- [ ] **Step 2 — RED read tests:** require:
  - `GET /repos/{owner}/{repo}/contents/{quoted-path}?ref={quoted-ref}`;
  - contents response must be `type == "file"` with non-empty `sha`;
  - follow with `GET /repos/{owner}/{repo}/git/blobs/{sha}`;
  - strict Base64 decode of blob JSON `content` and `encoding == "base64"`;
  - returned `GitHubFile.blob_sha` exactly equals contents SHA;
  - malformed/non-file/invalid-base64 responses become `GitHubApiError`.

- [ ] **Step 3 — RED tree tests:** require `GET /repos/{owner}/{repo}/git/trees/{quoted-ref}?recursive=1`, exact entry parsing, and preservation of `truncated`.

- [ ] **Step 4 — RED mutation tests:** require Base64 `content`, `branch`, deterministic `message`, and `sha` only for update/delete. Verify create/update parse optional content SHA and commit SHA; delete accepts its documented success response without requiring content metadata.

- [ ] **Step 5 — RED header/error tests:** every request has `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2026-03-10`, and `Authorization: Bearer <token>` only when token is supplied. HTTP 401/403/404/409/422 and transport failures become `GitHubApiError(status=...)` with no token in the message. Malformed JSON is `GitHubApiError`.

- [ ] **Step 6 — Run focused RED:** `python -m unittest tests.test_workflow_v2_github_api -v`. Expected: missing `GitHubRestClient`/HTTP behavior only.

- [ ] **Step 7 — Implement `_request_json`:** use injected opener or `urllib.request.build_opener()`, JSON encode request bodies deterministically, decode UTF-8 response JSON, catch `urllib.error.HTTPError`, `URLError`, timeout/OSError, and sanitize messages. No retries.

- [ ] **Step 8 — Implement `get_file/get_tree/create/update/delete`:** quote path/ref segments with `urllib.parse.quote`; validate response shapes; strict Base64 decode; construct Task 1 value objects.

- [ ] **Step 9 — GREEN focused + full matrix.**

- [ ] **Step 10 — Commit:** `feat: add GitHub REST storage transport`.

---

### Task 4: Domain parity on GitHubStorage

**Files:**
- Create: `tests/test_workflow_v2_github_backend_domain.py`
- Production: none unless a test exposes an actual backend-boundary defect.

**Interfaces:**
- Consumes: `WorkflowStateRepository(GitHubStorage(fake_client, repository="owner/repo", branch="work", root_prefix="books/sample"))`.
- Produces: evidence that existing domain operations need no GitHub-specific implementation branches.

- [ ] **Step 1 — Build reusable fake repository state:** use the same fake API client from the GitHub storage tests or move it to a non-discovered `tests/github_fake.py` helper. Seed canonical metadata/progress/ledger/manifest bytes through `WorkflowStateRepository`, not by bypassing schemas.

- [ ] **Step 2 — Claim parity test:** acquire a translator claim, prove a second session conflicts, release by owner, and prove durable audit/claim state matches filesystem semantics.

- [ ] **Step 3 — Coordination parity test:** create a coordination lease, prove a concurrent live lease conflicts, release it, and reacquire.

- [ ] **Step 4 — Status parity test:** create one extracted unit and resolve status/resume read-only; assert no mutation calls occurred during status resolution.

- [ ] **Step 5 — CAS state-transition parity test:** exercise an existing repository/domain write using a stale revision and prove the concurrent winner is preserved.

- [ ] **Step 6 — Run tests:** if all pass immediately, record this as parity coverage with no production change. If a real defect appears, use systematic debugging and make the smallest backend-boundary fix only.

- [ ] **Step 7 — Commit test-only parity coverage separately from any production fix.**

---

### Task 5: Capability and orchestration documentation

**Files:**
- Modify: `docs/ORCHESTRATION.md`
- Modify: `docs/AGENT_SETUP.md`
- Test: `tests/test_agent_contract.py`

**Interfaces:** documentation only; no literary contract changes.

- [ ] **Step 1 — RED contract tests:** assert orchestration/setup documentation states:
  - GitHub API backend is an allowed execution substrate when available;
  - durable GitHub reads/writes do not require GitHub Actions;
  - Contents read is needed for read-only operations and Contents write for mutations;
  - CAS conflict must cause re-read/replan, not blind retry;
  - source/privacy rules remain unchanged;
  - `docs/TRANSLATION.md` does not mention GitHub client/API details.

- [ ] **Step 2 — Run focused RED:** `python -m unittest tests.test_agent_contract -v`.

- [ ] **Step 3 — Update `docs/ORCHESTRATION.md`:** add a compact “GitHub-backed execution” section near execution modes/single-writer rules. Describe GitHubStorage as an orchestrator/runtime capability, not a literary concern.

- [ ] **Step 4 — Update `docs/AGENT_SETUP.md`:** document capability prerequisites, read-only vs write behavior, explicit branch/repository/root scope, token/credential non-persistence, and failure behavior. Do not instruct users to enable Actions for core execution.

- [ ] **Step 5 — GREEN agent contract + full matrix.**

- [ ] **Step 6 — Commit:** `docs: document GitHub-backed Workflow v2 execution`.

---

### Task 6: #18 GitHub backend reliability

**Files:**
- Create: `tests/test_workflow_v2_github_backend_reliability.py`
- Production: only when failure injection demonstrates a real defect.

**Interfaces:** uses fake-client mutation hooks from Task 2 and real domain managers where relevant.

- [ ] **Step 1 — Add failure-injection scenarios:** 
  1. another writer changes target after pre-read but before update => conflict, winner preserved;
  2. another writer overwrites immediately after successful update => read-back conflict, winner preserved;
  3. transport failure during mutation => exactly one mutation attempt and no hidden retry;
  4. truncated tree => list/status/claim discovery fails closed rather than using partial state;
  5. claim create race => one owner succeeds and loser receives recoverable `ClaimConflict`/storage conflict behavior;
  6. fresh read after conflict observes authoritative winner and allows normal caller recovery.

- [ ] **Step 2 — Run focused reliability suite:** `python -m unittest tests.test_workflow_v2_github_backend_reliability -v`.

- [ ] **Step 3 — Preserve evidence:** tests that immediately pass are test-only coverage; for genuine RED, capture exact failure before minimum production fix using `systematic-debugging`.

- [ ] **Step 4 — Full matrix GREEN.**

- [ ] **Step 5 — Commit reliability tests separately from any production fix.**

---

### Task 7: Final verification and integration audit

**Files:** no intended production changes.

- [ ] **Step 1 — Exact-head full suite:** verify `python -m unittest discover -s tests -v` succeeds on Python 3.10 and 3.12 for the exact final feature head; capture run id and exact test count.

- [ ] **Step 2 — Acceptance audit:** map tests/evidence to #17:
  - core coordination through GitHub backend without Actions;
  - CAS conflicts surfaced/recoverable;
  - common backend contract passes filesystem + GitHub;
  - no GitHub client logic in literary/domain contracts;
  - read/write capability failures concise and fail-closed.

- [ ] **Step 3 — Scope audit:** changed files should be limited to design/plan, `github_api.py`, `github_storage.py`, package exports, backend/parity/reliability tests, `docs/ORCHESTRATION.md`, `docs/AGENT_SETUP.md`, and the corresponding contract test. Any domain production change requires a documented test-proven reason.

- [ ] **Step 4 — PR audit:** base is `refactor/workflow-engine-v2`; branch is behind by 0 or safely updated; merge base is expected integration ancestry; no unresolved comments/reviews/threads; `main` remains unchanged.

- [ ] **Step 5 — Update PR body:** include RED/GREEN run ids, exact final test count, backend contract/parity/reliability mapping, final base/head SHAs, and explicit statement that Actions are not a runtime dependency.

- [ ] **Step 6 — Ready + merge:** mark Ready only after all guards pass. Merge with `expected_head_sha` into `refactor/workflow-engine-v2` only. Preserve `feature/workflow-v2-github-backend`. Never merge `main`.
