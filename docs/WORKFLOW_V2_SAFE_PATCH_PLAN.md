# Workflow v2 Safe Text Patching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, UTF-8-safe, CAS-protected text patch helper and `book.py patch` command for exact or regex small corrections with expected-count, line-scope, dry-run and unified-diff safety gates.

**Architecture:** Implement matching and mutation in backend-neutral `workflow_v2.text_patch`, using `StorageBackend.read()` plus `write_if_version()` for optimistic concurrency. Add a thin `patch_cli.py` adapter registered by `book.py`; export only the reusable domain API from `workflow_v2.__init__`. Test domain behavior before CLI wiring, then verify full regression matrix and PR boundaries.

**Tech Stack:** Python 3.10/3.12, stdlib `re`, `difflib`, `dataclasses`, existing `StorageBackend`/`FilesystemStorage`, `argparse`, `unittest`.

**Spec:** `docs/WORKFLOW_V2_SAFE_PATCH_DESIGN.md`

## Global Constraints

- Work only on `feature/workflow-v2-safe-patch`, based on `refactor/workflow-engine-v2` at `a288b6bddde65a064a38964d99b9d6c0f65749e6`.
- PR target is `refactor/workflow-engine-v2`; never modify or merge to `main`.
- Do not delete branches.
- Literal matching is default; regex requires explicit `--regex`.
- `--expected-count` is mandatory and must be a strict integer >= 0.
- Paths are repository-relative and resolved by `FilesystemStorage(repo_root())`; never bypass storage path-safety.
- Strict UTF-8 bytes in/out; do not normalize line endings through text-mode filesystem APIs.
- No fuzzy/semantic matching, multi-file transaction, automatic review/lifecycle mutation, or GitHub-backend implementation.
- No production code before a failing test demonstrates the missing behavior.

---

## File Map

### Create
- `scripts/workflow_v2/text_patch.py` — domain patch algorithm, validation, diff and CAS write.
- `scripts/workflow_v2/patch_cli.py` — argparse registration and stable CLI output/error adaptation.
- `tests/test_workflow_v2_text_patch.py` — domain RED/GREEN coverage.
- `tests/test_workflow_v2_patch_cli.py` — end-to-end CLI RED/GREEN coverage.

### Modify
- `scripts/workflow_v2/__init__.py` — export `TextPatchError`, `TextPatchResult`, `patch_text`.
- `scripts/book.py` — import/register patch CLI and catch `PatchCliError`.

---

### Task 1: Domain safety core — literal replacement, expected count, dry-run and UTF-8

**Files:**
- Create: `tests/test_workflow_v2_text_patch.py`
- Create after RED: `scripts/workflow_v2/text_patch.py`
- Modify after RED: `scripts/workflow_v2/__init__.py`

**Interfaces:**
- Consumes: `StorageBackend`, `StoredValue`, `StorageError`, `StorageVersionConflict`.
- Produces:

```python
class TextPatchError(RuntimeError): ...

@dataclass(frozen=True)
class TextPatchResult:
    path: str
    match_count: int
    changed: bool
    dry_run: bool
    original_version: str
    new_version: str | None
    diff: str


def patch_text(
    storage: StorageBackend,
    path: str,
    *,
    old: str,
    new: str,
    expected_count: int,
    regex: bool = False,
    line_start: int | None = None,
    line_end: int | None = None,
    dry_run: bool = False,
) -> TextPatchResult: ...
```

- [ ] **Step 1: Write RED tests for literal count/no-write semantics**

Create a temp `FilesystemStorage` and assert:

```python
with self.assertRaisesRegex(TextPatchError, "expected 2 match.*observed 1"):
    patch_text(storage, "sample.md", old="alpha", new="beta", expected_count=2)
self.assertEqual(storage.read("sample.md"), before)
```

Also add:
- success replaces exactly two non-overlapping literal occurrences;
- `expected_count=0` returns `match_count=0`, `changed=False`, `new_version=None`, with identical stored version/content;
- `old == new` with observed matches returns `changed=False` and no rewrite;
- empty literal `old` raises before write;
- negative or boolean `expected_count` raises before write.

- [ ] **Step 2: Verify RED in CI**

Commit tests only and use a draft PR to `refactor/workflow-engine-v2` as CI harness. Expected failure: import/module/API missing, not fixture/syntax error.

- [ ] **Step 3: Implement minimal literal domain behavior**

In `text_patch.py`:
- validate strict integer count;
- `storage.read(path)` baseline once;
- strict UTF-8 decode;
- choose whole text as scope initially when no line bounds;
- literal `scope.count(old)` / `scope.replace(old,new)`;
- exact expected-count gate;
- if no byte change, return no-write result;
- otherwise generate unified diff and call `write_if_version` once with baseline version;
- translate expected storage/Unicode errors into `TextPatchError` with concise context.

Export domain API from `workflow_v2.__init__`.

- [ ] **Step 4: Verify GREEN for Task 1**

Require the focused domain tests to pass on Python 3.10 and 3.12, plus existing storage tests.

- [ ] **Step 5: Commit GREEN**

Use an audit-friendly message such as `feat: add safe literal text patch domain`.

---

### Task 2: Line scope, line-ending preservation, regex and CAS conflict

**Files:**
- Modify: `tests/test_workflow_v2_text_patch.py`
- Modify after RED: `scripts/workflow_v2/text_patch.py`

**Interfaces:**
- Extends the exact `patch_text()` API from Task 1; no new public API.

- [ ] **Step 1: Write RED tests for line scope and raw-byte preservation**

Add tests proving:
- Cyrillic/Unicode replacement encodes exact expected UTF-8;
- multi-line literal `"first\n\nsecond"` crosses a paragraph boundary;
- input containing `b"one\r\ntwo\nthree\r\n"` preserves every untouched newline byte after replacing only `two`;
- `line_start=2, line_end=3` counts/replaces only physical lines 2–3;
- omitted start/end expand to file boundaries;
- line 0, negative, boolean, start>end, end>line-count and explicit range on empty file all fail with no write.

- [ ] **Step 2: Verify RED and implement line-slice algorithm**

Use `splitlines(keepends=True)` and reconstruct `prefix + updated_scope + suffix`. No `Path.read_text/write_text` may be introduced.

- [ ] **Step 3: Write RED tests for regex behavior**

Cover:

```python
result = patch_text(
    storage,
    "sample.md",
    old=r"^(Term):\s+(.+)$",
    new=r"\1 — \2",
    expected_count=1,
    regex=True,
)
```

with a pattern that actually matches the complete scope under the chosen flags. Add invalid regex and invalid replacement backreference tests; both must leave bytes/version unchanged.

- [ ] **Step 4: Implement regex with `re.compile()` + `subn()`**

Catch `re.error` for pattern and replacement execution and report `TextPatchError`. Do not silently fall back to literal mode.

- [ ] **Step 5: Write RED CAS-conflict injection test**

Create a `FilesystemStorage` subclass whose first `write_if_version()` call first commits a different winning payload via a second storage instance, then delegates the stale patch write. Assert:
- `patch_text()` raises a deterministic conflict `TextPatchError`;
- winner bytes remain complete;
- patch bytes are absent.

- [ ] **Step 6: Implement minimal conflict adaptation and verify GREEN**

Catch `StorageVersionConflict` separately and report that the target changed before patch commit. Run domain + storage tests on both Python versions.

- [ ] **Step 7: Commit Task 2**

Suggested message: `feat: add scoped regex safe patching`.

---

### Task 3: `book.py patch` CLI integration

**Files:**
- Create: `tests/test_workflow_v2_patch_cli.py`
- Create after RED: `scripts/workflow_v2/patch_cli.py`
- Modify after RED: `scripts/book.py`

**Interfaces:**
- Consumes `patch_text()` from Task 1/2.
- Produces:

```python
class PatchCliError(RuntimeError): ...

def register_patch_command(
    subparsers: argparse._SubParsersAction,
    root: Path,
) -> None: ...
```

- [ ] **Step 1: Write RED CLI smoke test**

Copy `book.py` + `workflow_v2/` into a temp repo, create `books/demo/translated/001.md`, and run:

```text
book.py patch books/demo/translated/001.md --old alpha --new beta --expected-count 1
```

Assert exit 0, exact target content, unified diff labels `a/...` and `b/...`, and summary:

```text
patch books/demo/translated/001.md: matches=1 changed=yes mode=apply
```

Expected RED: parser reports unknown `patch` command.

- [ ] **Step 2: Add RED error/no-write CLI cases**

Cover:
- count mismatch -> exit 1, one `ERROR:` line, no traceback, no mutation;
- `--dry-run` -> diff + `mode=dry-run`, no mutation;
- unsafe `../outside.md` -> exit 1/no outside write;
- negative `--expected-count` -> exit 1;
- regex + line-start/end changes only intended scoped occurrence.

- [ ] **Step 3: Implement `patch_cli.py`**

Create strict non-negative integer argparse type for `--expected-count`, positive integer types for line bounds, register the command, instantiate `FilesystemStorage(root)`, call `patch_text`, print diff when non-empty then deterministic summary. Convert domain/storage expected failures to `PatchCliError`; never import `book.py`.

- [ ] **Step 4: Wire `book.py`**

Import `PatchCliError`, `register_patch_command`; register after build parser creation and before/alongside existing Workflow v2 commands; add `PatchCliError` to the top-level expected exception tuple.

- [ ] **Step 5: Verify GREEN**

Run CLI tests plus book CLI, storage, review validation and status tests to prove parser registration did not regress existing commands.

- [ ] **Step 6: Commit Task 3**

Suggested message: `feat: add safe patch cli`.

---

### Task 4: Full verification, audit and integration

**Files:**
- PR metadata only unless verification reveals a genuine defect.

- [ ] **Step 1: Full matrix**

Require fresh `python -m unittest discover -s tests -v` CI success on Python 3.10 and 3.12 for the final head. Record run ID and total tests.

- [ ] **Step 2: Diff audit**

Compare base/head and verify only the approved files changed; branch must be `behind_by=0`; no unrelated workflow, book content or private source file is present.

- [ ] **Step 3: Review audit**

Fetch PR comments, reviews and inline threads. Resolve any blocking finding and rerun CI after code changes.

- [ ] **Step 4: Update PR evidence and Ready state**

PR body must include RED/GREEN run IDs, acceptance coverage, changed files, final matrix count, base/head SHA, `main` unchanged, branch preserved.

- [ ] **Step 5: Integrate only to `refactor/workflow-engine-v2`**

Under the standing autonomous project directive, merge only if head/base/CI/review guards still match. Use expected-head guard. Preserve the feature branch. Never merge to `main`.

---

## Self-Review Checklist

- Literal, regex, count gate, dry-run, line scope, UTF-8, paragraph boundaries and line endings each map to explicit tests.
- Mismatch, invalid regex, invalid UTF-8, invalid scope and CAS conflict each prove no unintended write.
- Public signatures match the approved spec exactly.
- `PatchCliError` prevents import cycles.
- Repository-relative root is explicit and uses existing storage path safety.
- `workflow_v2.__init__` exports only domain API, not argparse adapter.
- No step mutates review/lifecycle state automatically.
- No GitHub backend or multi-file semantics leak into #13.
