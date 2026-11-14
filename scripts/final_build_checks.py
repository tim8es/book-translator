#!/usr/bin/env python3
"""Book-agnostic, fail-closed gates for state-repository final EPUB Actions.

The state repository owns workflow triggers and publishing. This public module
owns reusable admission/verification logic. No book state is ever modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,99}\Z")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_PATH = re.compile(
    r"books/([a-z0-9][a-z0-9-]{0,99})/\.workflow/final-build-request\.json\Z"
)


class FinalBuildError(RuntimeError):
    """An artifact does not have the necessary current evidence."""


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FinalBuildError(f"Missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise FinalBuildError(f"Expected JSON object: {path}")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FinalBuildError(message)


def _book(root: Path, slug: str) -> Path:
    _require(bool(SLUG.fullmatch(slug)), "Invalid book slug")
    book = root / "books" / slug
    _require(book.is_dir() and not book.is_symlink(), "Book workspace missing or symlinked")
    return book


def discover(root: Path, event_name: str, requested_slug: str,
             before: str, after: str) -> list[str]:
    """Select only explicit build requests; no automatic build of all books."""
    root = root.resolve()
    if event_name == "workflow_dispatch":
        slugs = [requested_slug]
    elif event_name == "push":
        _require(bool(SHA40.fullmatch(after)), "Invalid push revision")
        if SHA40.fullmatch(before) and before != "0" * 40:
            command = ["git", "-C", str(root), "diff", "--name-only", before, after]
        else:
            command = ["git", "-C", str(root), "diff-tree", "--root",
                       "--no-commit-id", "--name-only", "-r", after]
        try:
            changed = subprocess.check_output(command, text=True).splitlines()
        except (subprocess.CalledProcessError, OSError) as exc:
            raise FinalBuildError("Unable to enumerate build-request changes") from exc
        slugs = sorted({
            match.group(1) for name in changed
            if (match := REQUEST_PATH.fullmatch(name))
        })
    else:
        raise FinalBuildError("Unsupported final-build event")
    _require(0 < len(slugs) <= 25, "No valid, explicitly requested book for final build")
    for slug in slugs:
        book = _book(root, slug)
        _require((book / ".workflow/final-build-request.json").is_file(),
                 f"Final build request missing: {slug}")
    return slugs


def admit(root: Path, slug: str) -> dict:
    """Validate current request, source, hashes and latest independent PASS."""
    book = _book(root.resolve(), slug)
    request = _read(book / ".workflow/final-build-request.json")
    metadata = _read(book / "metadata.json")
    manifest = _read(book / "source-manifest.json")
    progress = _read(book / "progress.json")
    ledger = _read(book / "review-ledger.json")
    workflow = metadata.get("workflow")
    source_meta = metadata.get("source")
    _require(isinstance(workflow, dict) and isinstance(source_meta, dict),
             "Missing workflow or original-source metadata")
    pin = workflow.get("resolved_revision")
    source_sha = source_meta.get("sha256")
    filename = source_meta.get("filename")
    _require(isinstance(pin, str) and bool(SHA40.fullmatch(pin)),
             "Invalid pinned workflow revision")
    _require(isinstance(source_sha, str) and bool(SHA64.fullmatch(source_sha)),
             "Invalid original source SHA-256")
    _require(isinstance(filename, str) and filename not in ("", ".", "..")
             and Path(filename).name == filename,
             "Unsafe original source filename")
    _require(request.get("schema_version") == 1
             and request.get("operation") == "finalize-and-build-epub"
             and request.get("book_slug") == slug, "Invalid final build request")
    _require(request.get("workflow_revision") == pin, "Workflow pin mismatch")
    _require(request.get("source_sha256") == source_sha, "Request source mismatch")
    _require(manifest.get("source_sha256") == source_sha, "Source manifest mismatch")
    chapters = progress.get("chapters")
    extracted = manifest.get("extracted")
    records = ledger.get("records")
    _require(isinstance(chapters, list) and len(chapters) > 0
             and isinstance(extracted, list) and isinstance(records, list),
             "Missing chapter or independent review evidence")
    _require(request.get("unit_count") == len(chapters) == len(extracted),
             "Build request chapter count mismatch")
    original = book / "source" / filename
    _require(original.is_file() and not original.is_symlink()
             and _sha(original) == source_sha, "Original source hash mismatch")
    claims = book / ".workflow/claims"
    _require(not claims.is_dir() or not any(claims.glob("chapter-*.json")),
             "Outstanding chapter claims")
    for blocker in ("coordination-lock.json", "source-promotion.json", "finalization.json"):
        _require(not (book / ".workflow" / blocker).exists(),
                 "Active workflow transition: " + blocker)
    sources = {entry.get("unit_id"): entry for entry in extracted
               if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str)}
    latest = {entry.get("unit_id"): entry for entry in records
              if isinstance(entry, dict) and isinstance(entry.get("unit_id"), str)}
    _require(len(sources) == len(extracted), "Duplicate or malformed source units")
    accepted_ids = set()
    for chapter in chapters:
        _require(isinstance(chapter, dict), "Malformed chapter")
        unit = chapter.get("unit_id")
        _require(isinstance(unit, str) and unit not in accepted_ids,
                 "Missing or duplicate progress unit")
        accepted_ids.add(unit)
        _require(chapter.get("status") == "reviewed", "Incomplete chapter: " + unit)
        source = sources.get(unit)
        _require(source is not None and source.get("path") == chapter.get("source_path"),
                 "Source path or unit mismatch: " + unit)
        translation_acceptance = chapter.get("translation_acceptance")
        _require(isinstance(translation_acceptance, dict),
                 "Missing translation acceptance: " + unit)
        for rel in (chapter.get("source_path"), chapter.get("translation_path")):
            _require(isinstance(rel, str) and bool(rel) and not Path(rel).is_absolute()
                     and ".." not in Path(rel).parts, "Unsafe chapter path: " + unit)
        original_part = book / chapter["source_path"]
        translated = book / chapter["translation_path"]
        _require(original_part.is_file() and translated.is_file()
                 and not original_part.is_symlink() and not translated.is_symlink(),
                 "Chapter text missing or symlinked: " + unit)
        source_hash, translation_hash = _sha(original_part), _sha(translated)
        _require(source_hash == source.get("sha256")
                 and translation_hash == translation_acceptance.get("translation_sha256"),
                 "Accepted text hash mismatch: " + unit)
        review = latest.get(unit)
        _require(review is not None and review.get("outcome") == "PASS"
                 and review.get("source_sha256") == source_hash
                 and review.get("translation_sha256") == translation_hash,
                 "Missing current independent PASS: " + unit)
    return {"ref": pin, "unit_count": len(chapters)}


def verify(root: Path, slug: str) -> dict:
    """Validate actual final EPUB bytes and supporting reports."""
    book = _book(root.resolve(), slug)
    manifest = _read(book / "output/manifest.json")
    progress = _read(book / "progress.json")
    chapters = progress.get("chapters")
    _require(isinstance(chapters, list) and len(chapters) > 0,
             "Missing progress chapters")
    _require(manifest.get("format") == "epub" and manifest.get("preview") is False,
             "Final output must be a reviewed EPUB")
    _require(manifest.get("unit_count") == len(chapters), "Final EPUB chapter count mismatch")
    artifact_path = manifest.get("artifact_path")
    _require(isinstance(artifact_path, str)
             and not Path(artifact_path).is_absolute()
             and ".." not in Path(artifact_path).parts
             and Path(artifact_path).suffix.lower() == ".epub",
             "Unsafe EPUB artifact path")
    artifact = (book / artifact_path).resolve()
    _require(artifact.is_relative_to((book / "output").resolve())
             and artifact.is_file(), "EPUB artifact missing from output")
    artifact_sha = _sha(artifact)
    _require(artifact_sha == manifest.get("artifact_sha256"), "Output hash mismatch")
    try:
        with zipfile.ZipFile(artifact) as epub:
            names = epub.namelist()
            _require(bool(names) and names[0] == "mimetype",
                     "EPUB mimetype must be first")
            _require(epub.read("mimetype") == b"application/epub+zip",
                     "Invalid EPUB mimetype")
            _require(epub.testzip() is None, "Corrupt EPUB member")
            _require("META-INF/container.xml" in names, "Missing EPUB container")
            _require(sum(name.endswith(".xhtml") for name in names) >= len(chapters),
                     "EPUB XHTML chapter count mismatch")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise FinalBuildError("EPUB ZIP cannot be verified") from exc
    for report in ("FINAL_QUALITY_GATES.md", "REVIEW_REPORT.md"):
        _require((book / report).is_file(), "Missing final report: " + report)
    return {"artifact": str(artifact), "sha256": artifact_sha,
            "unit_count": len(chapters)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("discover", "admit", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--book-slug", default="")
    parser.add_argument("--event-name", default="")
    parser.add_argument("--before", default="")
    parser.add_argument("--after", default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.operation == "discover":
            result = {"books": discover(args.root, args.event_name,
                                         args.book_slug, args.before, args.after)}
        elif args.operation == "admit":
            result = admit(args.root, args.book_slug)
        else:
            result = verify(args.root, args.book_slug)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as out:
                for key, value in result.items():
                    print(f"{key}={json.dumps(value) if isinstance(value, list) else value}", file=out)
    except (FinalBuildError, OSError, ValueError) as exc:
        print(f"Final-build gate blocked: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
