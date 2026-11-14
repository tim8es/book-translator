#!/usr/bin/env python3
"""Dispatch the pinned public book/corpus CLI against an external state checkout.

For historical immutable revisions whose book.py resolves its root from
__file__, the pinned checkout itself is the runtime root and its books/ entry
is a symlink to the state checkout. Only git HEAD queries are redirected to
the state Git repository. All code, docs, and provenance markers stay in the
pinned public checkout; the book's stored workflow pin is never rewritten.
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path

from pinned_runtime import prepare


def run(engine: Path, state: Path, revision: str, tool: str,
        arguments: list[str]) -> int:
    pinned = prepare(engine, state, revision)
    state = Path(state).resolve(strict=True)
    scripts = str(pinned / "scripts")
    if scripts in sys.path:
        sys.path.remove(scripts)
    sys.path.insert(0, scripts)
    # Old workflow revisions are immutable. Do not patch their source on disk.
    # Their common module accepts a dynamic REPO_ROOT on import, but the pinned
    # engine root itself is correct thanks to the books/ symlink.
    book = importlib.import_module("book")
    book.REPO_ROOT = pinned

    # Parallel dispatch and optional claim base revisions must reference the
    # book-state Git HEAD, never the public workflow commit.
    def state_head(_engine_root: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(state), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    for name in ("claim_cli", "review_cli", "status_cli", "epub_cli"):
        module = importlib.import_module("workflow." + name)
        if hasattr(module, "_git_head"):
            module._git_head = state_head
    if tool == "book":
        return int(book.main(arguments) or 0)
    if tool == "corpus":
        corpus = importlib.import_module("corpus")
        return int(corpus.main(arguments) or 0)
    raise ValueError("unsupported pinned CLI command")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("tool", choices=("book", "corpus"))
    args, remainder = parser.parse_known_args(argv)
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    if not remainder:
        parser.error("expected canonical CLI subcommand")
    return run(args.engine, args.state, args.revision, args.tool, remainder)


if __name__ == "__main__":
    raise SystemExit(main())
