"""Read-only source audit and explicit creation of missing legacy evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from .source_coverage import COVERAGE_PATH, SourceCoverageError, audit_epub


class CoverageCliError(RuntimeError):
    """The original source could not be verified against the active corpus."""


def coverage_command(args: argparse.Namespace, root: Path) -> int:
    book = sys.modules.get("__main__")
    if book is None or not hasattr(book, "load_book"):
        book = importlib.import_module("book")
    try:
        book_dir, metadata, progress = book.load_book(args.slug)
    except Exception as exc:
        raise CoverageCliError(f"cannot read book: {exc}") from exc
    if metadata.get("source_format") != "epub":
        raise CoverageCliError("source-coverage currently supports EPUB only")
    identity = metadata.get("source")
    if not isinstance(identity, dict):
        raise CoverageCliError("missing source identity")
    source = (
        Path(args.source).expanduser().resolve()
        if args.source else book_dir / "source" / str(metadata.get("source_file"))
    )
    if not source.is_file():
        raise CoverageCliError("original EPUB unavailable; supply --source ORIGINAL.epub")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != identity.get("sha256"):
        raise CoverageCliError("original EPUB SHA-256 differs from sealed source")
    if len(data) != identity.get("size_bytes"):
        raise CoverageCliError("original EPUB size differs from sealed source")
    if source.name != metadata.get("source_file"):
        raise CoverageCliError("original EPUB filename differs from metadata")

    chapters = []
    for item in progress.get("chapters", []):
        rel = item.get("source_path")
        if (not isinstance(rel, str) or Path(rel).is_absolute()
                or ".." in Path(rel).parts or Path(rel).parts[:1] != ("extracted",)):
            raise CoverageCliError("unsafe extracted source path")
        try:
            content = (book_dir / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise CoverageCliError(f"cannot read extracted unit {rel}: {exc}") from exc
        chapters.append(SimpleNamespace(title=item.get("title"), content=content))
    try:
        report = audit_epub(
            source, chapters, extract_document=book.split_html_document,
            decode_text=book.decode_text,
        )
    except (SourceCoverageError, ValueError, OSError, UnicodeError) as exc:
        raise CoverageCliError(f"source coverage blocked: {exc}") from exc

    if args.record:
        path = book_dir / COVERAGE_PATH
        payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        try:
            with path.open("xb") as handle:
                handle.write(payload)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise CoverageCliError("existing coverage evidence differs; refusing overwrite")
        except OSError as exc:
            raise CoverageCliError(f"cannot record evidence: {exc}") from exc
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"EPUB coverage VERIFIED: {len(report['documents'])} documents, "
            f"{report['chapter_count']} chapters"
        )
    return 0


def register_coverage_command(subparsers: argparse._SubParsersAction, root: Path, error_factory) -> None:
    parser = subparsers.add_parser(
        "source-coverage",
        help="Verify EPUB against original and optionally certify existing extracted chapters.",
    )
    parser.add_argument("slug")
    parser.add_argument("--source", help="Exact original EPUB; needed for private-source workspaces.")
    parser.add_argument("--record", action="store_true", help="Create missing evidence, never overwrite.")
    parser.add_argument("--json", action="store_true")
    def run(args):
        try:
            return coverage_command(args, root)
        except CoverageCliError as exc:
            raise error_factory(str(exc)) from exc
    parser.set_defaults(func=run)
