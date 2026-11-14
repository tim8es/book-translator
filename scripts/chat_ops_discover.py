#!/usr/bin/env python3
"""Discover ChatOps work by book without reading or mutating book contents.

The state repository owns its queue; the public executable owns validation
and grouping. Invalid requests go to a bounded __invalid__ lane so the
canonical bridge can persist failed receipts rather than losing work.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
BOOK_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,99}\Z")


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    values = {}
    for key, val in pairs:
        if key in values:
            raise ValueError("duplicate JSON key")
        values[key] = val
    return values


def pending_books(root: Path, limit: int = 200) -> list[str]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("invalid ChatOps discovery bound")
    requests = root / ".chat-ops" / "requests"
    results = root / ".chat-ops" / "results"
    names = set()
    for path in sorted(requests.glob("*.json")):
        if not REQUEST_ID.fullmatch(path.stem):
            continue
        if (results / path.name).exists():
            continue
        try:
            body = json.loads(path.read_text("utf-8"), object_pairs_hook=unique_pairs)
            slug = body.get("book_slug") if isinstance(body, dict) else None
            if not isinstance(slug, str) or not BOOK_SLUG.fullmatch(slug):
                slug = "__invalid__"
        except (OSError, ValueError, UnicodeError):
            slug = "__invalid__"
        names.add(slug)
    return sorted(names)[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path,
                        help="Book-state checkout, not the public engine checkout")
    parser.add_argument("--github-output", required=True, type=Path,
                        help="GITHUB_OUTPUT file for book-scoped matrix")
    args = parser.parse_args(argv)
    names = pending_books(args.root)
    with args.github_output.open("a", encoding="utf-8") as out:
        print("books=" + json.dumps(names or ["__none__"]), file=out)
        print("has_pending=" + str(bool(names)).lower(), file=out)
    print("Pending ChatOps book lanes:", len(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
