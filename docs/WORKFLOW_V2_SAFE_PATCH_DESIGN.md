# Workflow v2 — Safe text patching

Issue: #13
Branch: `feature/workflow-v2-safe-patch`
Base: `refactor/workflow-engine-v2` at `a288b6bddde65a064a38964d99b9d6c0f65749e6`
PR target: `refactor/workflow-engine-v2`
Date: 2026-09-06

## Purpose

Add a deterministic, repository-safe alternative to rewriting an entire text artifact when ChatGPT/Codex needs to make a small terminology or wording correction.

The patch operation must fail closed when the target bytes, match count or write revision differ from what the caller expected. It must preserve UTF-8 text and existing line endings outside the exact replaced spans, show the proposed diff, and support literal or explicitly requested regular-expression matching.

## Scope

In scope:

- one generic repository-relative text patch operation;
- exact literal replacement by default;
- optional regular-expression replacement;
- mandatory `--expected-count` safety precondition;
- optional inclusive line scope;
- dry-run mode;
- concise unified diff output;
- strict UTF-8 input/output;
- optimistic-concurrency protection through the existing filesystem storage revision token;
- integration with `scripts/book.py` as a structural workflow command;
- unit and CLI tests for mismatch/no-write, Unicode, paragraph boundaries and line-ending preservation.

Out of scope:

- semantic/fuzzy matching;
- automatic conflict resolution or three-way merge;
- patching binary files;
- rewriting multiple files in one transaction;
- automatically updating lifecycle/review state after translation changes;
- GitHub API backend execution (#17 will reuse the backend-neutral patch domain operation later if appropriate);
- changes to `main`.

## Selected architecture

Create a backend-neutral patch domain module:

```text
scripts/workflow_v2/text_patch.py
```

and a CLI adapter:

```text
scripts/workflow_v2/patch_cli.py
```

`text_patch.py` owns matching, scoping, UTF-8 decoding/encoding, count validation, diff generation and the optimistic write. It accepts the existing `StorageBackend` protocol, so filesystem execution uses the already-tested `FilesystemStorage` CAS semantics rather than introducing a second atomic-write implementation.

`patch_cli.py` owns argparse registration, repository-root path selection, user-visible errors and diff/summary printing. `book.py` only imports/registers the command.

This split keeps the patch behavior independently testable and leaves GitHub/backend-specific transport outside the literary/text logic.

## CLI contract

Primary command:

```bash
python scripts/book.py patch <path> \
  --old <text-or-pattern> \
  --new <replacement> \
  --expected-count <N> \
  [--regex] \
  [--line-start <N>] \
  [--line-end <N>] \
  [--dry-run]
```

Examples:

```bash
python scripts/book.py patch books/demo/translated/001.md \
  --old "old term" --new "preferred term" --expected-count 2
```

```bash
python scripts/book.py patch books/demo/glossary.md \
  --old '^(Term):\s+(.+)$' --new '\1 — \2' --expected-count 1 --regex \
  --line-start 20 --line-end 40 --dry-run
```

Rules:

- `<path>` is repository-relative and must resolve through the storage backend path-safety rules; absolute paths, `..`, `./`, backslash escapes and other unsafe forms are rejected before file mutation.
- `--old`, `--new` and `--expected-count` are required.
- `--expected-count` is an integer `>= 0`.
- literal matching is the default.
- `--regex` opts into Python regular-expression semantics for both pattern and replacement; replacement backreferences follow `re.sub` rules.
- `--line-start` and `--line-end` are 1-based inclusive line numbers. Either may be supplied independently; omitted start means line 1, omitted end means the final line.
- a requested line range must intersect valid file lines, use positive integers, and satisfy start <= end.
- matching/counting occurs only inside the selected line slice.
- a multi-line literal/regex can cross line boundaries inside the selected slice, including paragraph boundaries.
- matching never crosses outside the selected slice.
- `--dry-run` performs every read/validation/diff step but never calls the write primitive.

## Domain API

`text_patch.py` defines:

```python
class TextPatchError(RuntimeError):
    pass

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
) -> TextPatchResult:
    ...
```

The domain function reads exactly once for the baseline revision, computes the full replacement in memory, validates the observed count, prepares the diff, and writes only with `storage.write_if_version(path, updated_bytes, original.version)`.

A stale concurrent writer therefore causes the existing `StorageVersionConflict`; the patch layer converts it into a `TextPatchError` that explicitly says the target changed before the patch could commit. It does not re-read and silently retry because that would invalidate the caller's observed-count assumption.

## UTF-8 and line endings

The storage backend returns bytes. Patch processing uses strict `bytes.decode("utf-8")` and `str.encode("utf-8")`.

This deliberately avoids `Path.read_text()`/`write_text()` universal-newline behavior. Existing `LF`, `CRLF` or mixed newline bytes survive unchanged unless they are part of a replaced span.

A UTF-8 BOM is preserved naturally: decoding as plain UTF-8 produces U+FEFF and encoding restores the same BOM bytes unless the patch explicitly targets that first character.

Invalid UTF-8 raises `TextPatchError` before matching or writing.

## Line scope algorithm

Use `text.splitlines(keepends=True)` so physical line separators remain part of each line.

For non-empty text:

1. derive total physical line count from `splitlines(keepends=True)`;
2. normalize start to 1 when omitted;
3. normalize end to total line count when omitted;
4. reject start/end outside `1..total` or start > end;
5. split into `prefix`, `scope`, `suffix` by line indexes;
6. perform match count/replacement only on `scope`;
7. reconstruct `prefix + updated_scope + suffix` exactly.

For an empty file, no explicit line range is valid. Without line scope, the whole empty string is the scope and normal expected-count logic applies.

## Match-count semantics

### Literal mode

Use the same non-overlapping semantics as `str.replace`:

```python
match_count = scope.count(old)
updated_scope = scope.replace(old, new)
```

An empty literal `old` is rejected. Python's implicit insertion behavior for empty patterns is too easy to misuse for a safe patch command.

### Regex mode

Compile `old` with `re.compile(old)`. Invalid patterns are reported as `TextPatchError` before writing.

Use:

```python
updated_scope, match_count = pattern.subn(new, scope)
```

Invalid replacement/backreference syntax is also reported before writing.

Zero-width regular expressions are allowed only when the caller explicitly chose `--regex` and supplied the exact expected count; this is deterministic under `re.subn` and remains protected by count/CAS checks.

## Expected-count gate

Observed count must equal `expected_count` exactly.

On mismatch:

- return no partial result;
- do not call `write_if_version`;
- raise `TextPatchError` with both expected and observed counts;
- leave target bytes/revision unchanged.

`expected_count=0` is valid and can be used as an assertion that the pattern is absent. The result is `changed=false`; no write occurs even outside dry-run mode because there are no matched spans.

## No-op replacements

If matches exist but replacement produces byte-identical content (for example literal old == new), report the observed match count and `changed=false` and do not rewrite the file.

This prevents meaningless revision churn and keeps retry behavior deterministic.

## Diff output

Generate a standard unified diff with `difflib.unified_diff` from the original and updated full text using `splitlines(keepends=True)`.

Labels are deterministic:

```text
--- a/<path>
+++ b/<path>
```

The domain result stores the complete concise diff string. For unchanged content, `diff` is the empty string.

CLI behavior:

- print the diff first when non-empty;
- then print one summary line:

```text
patch <path>: matches=<N> changed=<yes|no> mode=<apply|dry-run>
```

No ANSI color is emitted, so output is stable for ChatGPT/Codex consumption.

## Concurrency and atomicity

Filesystem writes reuse `FilesystemStorage.write_if_version`, which already:

- validates safe logical paths;
- verifies the expected SHA-256 revision;
- rejects stale versions with `StorageVersionConflict`;
- atomically replaces successful writes;
- leaves no temporary file after success.

The patch helper does not bypass this backend.

A test storage can inject a concurrent update after the patch read but before its CAS write. The patch must surface a conflict and preserve the concurrent winner's complete content.

## Error handling

`TextPatchError` wraps expected patch-domain failures:

- non-integer/negative expected count at API validation;
- empty literal old text;
- invalid regex or replacement syntax;
- invalid UTF-8;
- invalid/out-of-range line scope;
- match-count mismatch;
- target missing/path unsafe/storage failure;
- stale write conflict.

The CLI adapter converts these into the existing `BookError` surface so `book.py` prints a single `ERROR: ...` message and exits 1.

No expected failure prints a Python traceback.

## Interaction with workflow state

The command is intentionally generic text mutation. It does **not** modify `progress.json`, review ledger, claims or source manifest automatically.

If the caller patches a translated artifact that already has PASS evidence, existing hash-bound review resolution will naturally mark that PASS stale on the next status/review/finalize check. This preserves the current single source of truth instead of duplicating review invalidation logic in the patch helper.

Likewise, patching generated Markdown or glossary/style text does not fabricate unrelated workflow state transitions.

## Testing strategy

### Domain tests — `tests/test_workflow_v2_text_patch.py`

Tests-first coverage:

1. literal expected-count mismatch raises and leaves bytes/version unchanged;
2. literal success replaces exactly the intended non-overlapping spans;
3. `expected_count=0` is a no-write assertion;
4. old == new is no-write despite observed matches;
5. dry-run returns the same diff/count as apply but leaves bytes/version unchanged;
6. Unicode/Cyrillic replacement remains exact UTF-8;
7. multi-line literal replacement can cross a paragraph boundary;
8. CRLF/mixed line endings outside replaced spans remain byte-identical;
9. inclusive line scope changes only selected lines and counts only inside them;
10. invalid/out-of-range scopes fail without writing;
11. regex substitution supports capture-group replacement and exact expected count;
12. invalid regex/replacement fails without writing;
13. invalid UTF-8 fails without writing;
14. injected stale CAS conflict preserves the concurrent winner and surfaces a deterministic patch error.

### CLI tests — `tests/test_workflow_v2_patch_cli.py`

Cover:

- successful literal patch through `book.py patch`;
- mismatch exit 1/no mutation;
- dry-run prints diff but does not write;
- regex and line scope flags reach the domain behavior;
- unsafe path is rejected;
- output summary/diff is deterministic and traceback-free.

### Regression suite

After focused GREEN, run the complete standard suite on Python 3.10 and 3.12. No GitHub Action is required to execute the command itself; Actions remains only the CI harness in this development environment.

## Expected implementation surface

```text
scripts/book.py
scripts/workflow_v2/text_patch.py
scripts/workflow_v2/patch_cli.py
scripts/workflow_v2/__init__.py   # export domain API only if consistent with existing package surface

tests/test_workflow_v2_text_patch.py
tests/test_workflow_v2_patch_cli.py

docs/WORKFLOW_V2_SAFE_PATCH_DESIGN.md
```

No other production file should change unless a test proves an existing shared primitive defect.

## Completion criteria

#13 is ready to integrate when:

- every acceptance criterion has deterministic automated coverage;
- count mismatch/dry-run/conflict paths are proven no-write;
- Unicode, paragraph-boundary and line-ending tests pass;
- CLI can safely patch translation/glossary/generated repository text by relative path;
- full Python 3.10/3.12 matrix is green;
- diff/PR/review-thread audit is clean;
- branch is not behind integration;
- `main` is unchanged;
- no branch is deleted.
