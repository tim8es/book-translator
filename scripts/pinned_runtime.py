"""Run immutable literary workflow code outside the book-state checkout.

The caller checks out a public repository at the book's exact pinned commit.
We add only a disposable installation marker and a books/ symlink to that
*public runtime checkout*. No script, template, contract, install marker, or
source book is copied into the state repository.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
PUBLIC_REPOSITORY = "https://github.com/tim8es/book-translator"


class PinnedRuntimeError(RuntimeError):
    """Unsafe, stale, or unavailable immutable public workflow runtime."""


def git_head(directory: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--verify", "HEAD"],
        text=True, capture_output=True, timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def prepare(engine: Path, state: Path, revision: str) -> Path:
    """Bind the exact public checkout to external state, fail closed on drift."""
    if not isinstance(revision, str) or SHA40.fullmatch(revision) is None:
        raise PinnedRuntimeError("literary workflow revision must be a 40-digit SHA")
    engine = Path(engine).resolve(strict=True)
    state = Path(state).resolve(strict=True)
    if engine == state or not (state / ".git").exists():
        raise PinnedRuntimeError("book-state checkout must be a separate Git repository")
    if git_head(engine) != revision:
        raise PinnedRuntimeError("literary workflow checkout differs from book pin")
    if not (engine / "scripts" / "book.py").is_file():
        raise PinnedRuntimeError("pinned canonical CLI is unavailable")
    books = state / "books"
    if not books.is_dir() or books.is_symlink():
        raise PinnedRuntimeError("book-state checkout requires a real books directory")
    link = engine / "books"
    if link.is_symlink():
        if link.resolve(strict=True) != books.resolve(strict=True):
            raise PinnedRuntimeError("pinned books link points outside the selected state")
    elif link.is_dir() and not link.is_symlink():
        # The canonical public repository tracks only books/.gitkeep. This is
        # a non-content placeholder, not a book or implementation file. The
        # separate disposable checkout can drop it before linking state.
        # Refuse any other tracked/untracked files rather than hiding them.
        contents = list(link.iterdir())
        if (len(contents) != 1 or contents[0].name != ".gitkeep"
                or not contents[0].is_file() or contents[0].is_symlink()
                or contents[0].stat().st_size != 0):
            raise PinnedRuntimeError("pinned public checkout unexpectedly contains books/")
        contents[0].unlink()
        link.rmdir()
        link.symlink_to(books.resolve(strict=True), target_is_directory=True)
    elif link.exists():
        raise PinnedRuntimeError("unsafe pinned books placeholder")
    else:
        link.symlink_to(books.resolve(strict=True), target_is_directory=True)
    marker = engine / ".book-translator-install.json"
    if marker.is_symlink():
        raise PinnedRuntimeError("unsafe pinned installation marker symlink")
    if marker.exists() and marker.is_file():
        try:
            current = json.loads(marker.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise PinnedRuntimeError("invalid existing pinned installation marker") from exc
        if (current.get("resolved_revision") != revision or
                current.get("canonical_repository") != PUBLIC_REPOSITORY):
            raise PinnedRuntimeError("pinned installation marker disagrees with checkout")
    elif marker.exists():
        raise PinnedRuntimeError("pinned installation marker must be a regular file")
    else:
        marker.write_text(json.dumps({
            "schema_version": 1, "canonical_repository": PUBLIC_REPOSITORY,
            "requested_ref": revision, "resolved_revision": revision,
            "install_root": ".",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return engine
