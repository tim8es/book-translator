#!/usr/bin/env python3
"""Fail-closed, book-agnostic final EPUB delivery from a pinned public engine.

All writes occur in a disposable scratch checkout; the source repository is
never changed. This tool does not upgrade a book's workflow revision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PUBLIC_REPO = "https://github.com/tim8es/book-translator"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")


class DeliveryError(RuntimeError):
    """The requested deliverable has not passed admission or verification."""


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeliveryError(f"Missing or invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise DeliveryError(f"Expected object in {path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight(repository: Path, slug: str) -> dict:
    """Read-only evidence gate; never infer readiness from a mere status label."""
    if not SLUG.fullmatch(slug):
        raise DeliveryError("Invalid book slug")
    root = repository.resolve(strict=True)
    book = root / "books" / slug
    if book.is_symlink() or not book.is_dir():
        raise DeliveryError("Book workspace does not exist or is a symlink")
    metadata = _load_json(book / "metadata.json")
    progress = _load_json(book / "progress.json")
    coverage = _load_json(book / "source-coverage.json")
    workflow = metadata.get("workflow") or {}
    pin = workflow.get("resolved_revision")
    if not isinstance(pin, str) or not SHA40.fullmatch(pin):
        raise DeliveryError("Missing immutable book workflow revision")
    if workflow.get("repository", PUBLIC_REPO) != PUBLIC_REPO:
        raise DeliveryError("Book is pinned to an unexpected workflow repository")
    source = metadata.get("source") or {}
    source_sha = source.get("sha256")
    filename = source.get("filename")
    if not isinstance(source_sha, str) or not SHA64.fullmatch(source_sha):
        raise DeliveryError("Missing source SHA-256")
    if not isinstance(filename, str) or filename in ("", ".", "..") or Path(filename).name != filename:
        raise DeliveryError("Unsafe or missing original source filename")
    original = book / "source" / filename
    if original.is_symlink() or not original.is_file():
        raise DeliveryError("Original source bytes are unavailable")
    if _sha256(original) != source_sha:
        raise DeliveryError("Original source SHA-256 differs from metadata")
    if coverage.get("state") != "verified" or coverage.get("source_sha256") != source_sha:
        raise DeliveryError("Current independent source coverage is not verified")
    chapters = progress.get("chapters")
    if not isinstance(chapters, list) or not chapters or any(
        not isinstance(ch, dict) or ch.get("status") != "reviewed" for ch in chapters
    ):
        raise DeliveryError("Not all source units have accepted reviews")
    if progress.get("book_slug") not in (None, slug):
        raise DeliveryError("Progress belongs to another book")
    workflow_dir = book / ".workflow"
    for blocker in ("coordination-lock.json", "source-promotion.json", "finalization.json"):
        if (workflow_dir / blocker).exists():
            raise DeliveryError(f"Concurrent or incomplete transition: {blocker}")
    claims = workflow_dir / "claims"
    if claims.is_dir() and any(claims.glob("chapter-*.json")):
        raise DeliveryError("Outstanding chapter claims require orchestration")
    return {"slug": slug, "pin": pin, "source_sha256": source_sha,
            "unit_count": len(chapters)}


def verify_epub(book: Path, expected_count: int) -> dict:
    """Independently check the built artifact against its canonical manifest."""
    manifest = _load_json(book / "output" / "manifest.json")
    if manifest.get("format") != "epub" or manifest.get("preview") is not False:
        raise DeliveryError("Final manifest is not a reviewed EPUB")
    if manifest.get("unit_count") != expected_count:
        raise DeliveryError("Final manifest has a wrong source-unit count")
    artifact_path = manifest.get("artifact_path")
    if not isinstance(artifact_path, str):
        raise DeliveryError("Final manifest lacks an artifact path")
    rel = Path(artifact_path)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() != ".epub":
        raise DeliveryError("Unsafe or unexpected EPUB artifact path")
    artifact = (book / rel).resolve()
    if not artifact.is_relative_to((book / "output").resolve()) or not artifact.is_file():
        raise DeliveryError("EPUB artifact is missing from the output directory")
    actual_sha = _sha256(artifact)
    if actual_sha != manifest.get("artifact_sha256"):
        raise DeliveryError("Final EPUB SHA-256 does not match its manifest")
    try:
        with zipfile.ZipFile(artifact) as archive:
            members = archive.namelist()
            if not members or members[0] != "mimetype":
                raise DeliveryError("EPUB mimetype is not the first ZIP member")
            if archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
                raise DeliveryError("EPUB mimetype must be stored without compression")
            if archive.read("mimetype") != b"application/epub+zip":
                raise DeliveryError("Invalid EPUB mimetype")
            if "META-INF/container.xml" not in members or archive.testzip() is not None:
                raise DeliveryError("Corrupt or incomplete EPUB ZIP")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DeliveryError("EPUB is unreadable") from exc
    for report in ("STATE.md", "FINAL_QUALITY_GATES.md", "REVIEW_REPORT.md"):
        if not (book / report).is_file():
            raise DeliveryError(f"Missing final report: {report}")
    return {"artifact_path": str(artifact), "artifact_sha256": actual_sha,
            "unit_count": expected_count}


def _command(args: list[str], cwd: Path) -> str:
    # Do not print captured stdout/stderr: some engine errors include excerpts
    # of copyrighted source or translated material.
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise DeliveryError(f"Engine step {args[1:4]!r} failed with exit {result.returncode}")
    return result.stdout


def build(repository: Path, slug: str, scratch: Path) -> dict:
    plan = preflight(repository, slug)
    scratch = scratch.resolve()
    if scratch.exists():
        raise DeliveryError("Scratch target already exists; refusing overwrite")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    _command(["git", "clone", "--quiet", PUBLIC_REPO, str(scratch)], scratch.parent)
    _command(["git", "-C", str(scratch), "checkout", "--quiet", "--detach", plan["pin"]],
             scratch.parent)
    actual = _command(["git", "-C", str(scratch), "rev-parse", "HEAD"], scratch.parent).strip()
    if actual != plan["pin"]:
        raise DeliveryError("Fetched engine revision does not match the book pin")
    book = scratch / "books" / slug
    shutil.copytree(repository.resolve() / "books" / slug, book, dirs_exist_ok=False)
    marker = {
        "schema_version": 1, "canonical_repository": PUBLIC_REPO,
        "requested_ref": plan["pin"], "resolved_revision": plan["pin"],
        "install_root": "."
    }
    (scratch / ".book-translator-install.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # The book-pinned CLI is not guaranteed to expose EPUB/finalize flags.
    # All audited revisions expose these canonical library entrypoints.
    _command([sys.executable, "scripts/book.py", "validate", slug], scratch)
    _command([sys.executable, "scripts/corpus.py", "verify", slug], scratch)
    engine_command = """
import argparse
from pathlib import Path
from workflow.finalize_cli import finalize_command
from workflow.epub_cli import epub_build_command, build_status_command
from workflow.status_cli import default_preflight
root = Path.cwd()
slug = __import__("sys").argv[1]
result = finalize_command(
    argparse.Namespace(slug=slug, session_id=None, json=False),
    root, preflight=lambda name: default_preflight(root, name),
)
if result != 0:
    raise SystemExit("Pinned finalization rejected the book")
result = epub_build_command(
    argparse.Namespace(slug=slug, output=None, allow_unreviewed=False), root,
)
if result != 0:
    raise SystemExit("Pinned EPUB renderer failed")
result = build_status_command(
    argparse.Namespace(slug=slug, format="epub", allow_unreviewed=False, json=True),
    root,
)
if result != 0:
    raise SystemExit("Pinned build-status check failed")
"""
    _command([sys.executable, "-c", engine_command, slug], scratch)
    delivered = verify_epub(book, plan["unit_count"])
    return {**plan, **delivered}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, type=Path,
                        help="Checkout holding books/<slug>/; never modified")
    parser.add_argument("--book", required=True, help="Canonical book slug")
    parser.add_argument("--scratch", type=Path,
                        help="New disposable location for the pinned engine and outputs")
    parser.add_argument("--preflight-only", action="store_true",
                        help="Validate original, review states and book pin without writes")
    args = parser.parse_args(argv)
    try:
        if args.preflight_only:
            result = preflight(args.repository, args.book)
        else:
            if args.scratch is None:
                parser.error("--scratch is required unless --preflight-only is set")
            result = build(args.repository, args.book, args.scratch)
    except (DeliveryError, OSError) as exc:
        print(f"Final delivery blocked: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
