#!/usr/bin/env python3
"""Integrity and batch-recovery helper for Book Translator source corpora.

This helper complements scripts/book.py. It records and verifies a durable SHA-256
manifest for the preserved source and extracted artifacts, and can reconstruct the
complete extracted corpus from one verified source file without mutating translation
state.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import book
from workflow_v2 import (
    LoadedDocument,
    RepositoryError,
    SchemaError,
    SchemaKind,
    StorageError,
    WorkflowStateRepository,
)
from workflow_v2.schemas import SCHEMA_VERSION, parse_document


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path(book_dir: Path) -> Path:
    return book_dir / "source-manifest.json"


def load_source_manifest_document(book_dir: Path) -> LoadedDocument | None:
    path = manifest_path(book_dir)
    if not path.is_file():
        return None
    try:
        return book.state_repository(book_dir).read(
            "source-manifest.json",
            SchemaKind.SOURCE_MANIFEST,
        )
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise book.BookError(f"Invalid source-manifest.json: {exc}") from exc


def load_source_manifest(book_dir: Path) -> dict | None:
    document = load_source_manifest_document(book_dir)
    return document.data if document is not None else None


def write_source_manifest(
    book_dir: Path,
    data: dict,
    *,
    expected_version: str | None = None,
    create_only: bool = False,
) -> str:
    repository: WorkflowStateRepository = book.state_repository(book_dir)
    try:
        if expected_version is not None:
            return repository.write_if_version(
                "source-manifest.json",
                SchemaKind.SOURCE_MANIFEST,
                data,
                expected_version,
            )
        if create_only:
            return repository.create(
                "source-manifest.json",
                SchemaKind.SOURCE_MANIFEST,
                data,
            )

        # `seal` intentionally replaces an existing manifest. Obtain the current
        # raw revision without requiring the old manifest to be schema-valid, so
        # resealing can repair a malformed prior manifest while still using CAS.
        if manifest_path(book_dir).is_file():
            current = repository.storage.read("source-manifest.json")
            return repository.write_if_version(
                "source-manifest.json",
                SchemaKind.SOURCE_MANIFEST,
                data,
                current.version,
            )
        return repository.create(
            "source-manifest.json",
            SchemaKind.SOURCE_MANIFEST,
            data,
        )
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise book.BookError(f"Cannot write source-manifest.json: {exc}") from exc


def checked_source_rel(book_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise book.BookError(f"Invalid extracted source path in progress.json: {value!r}")
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != "extracted":
        raise book.BookError(f"Source path must stay under extracted/: {value}")
    target = (book_dir / rel).resolve()
    extracted_root = (book_dir / "extracted").resolve()
    try:
        target.relative_to(extracted_root)
    except ValueError as exc:
        raise book.BookError(f"Source path escapes extracted/: {value}") from exc
    return rel


def source_file_path(book_dir: Path, metadata: dict) -> Path:
    source_file = metadata.get("source_file")
    if not isinstance(source_file, str) or not source_file or Path(source_file).name != source_file:
        raise book.BookError(f"Invalid metadata source_file: {source_file!r}")
    return book_dir / "source" / source_file


def normalized_expected_sha(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        raise book.BookError(f"{label} must be a 64-character hexadecimal SHA-256 value")
    return normalized


def build_manifest(book_dir: Path, metadata: dict, progress: dict, source: Path) -> dict:
    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise book.BookError("progress.json must contain a chapters array")

    extracted: list[dict] = []
    for record in chapters:
        if not isinstance(record, dict):
            raise book.BookError("Every chapter entry must be an object")
        rel = checked_source_rel(book_dir, record.get("source_path"))
        path = book_dir / rel
        if not path.is_file():
            raise book.BookError(f"Cannot seal incomplete corpus; missing {rel.as_posix()}")
        extracted.append(
            {
                "number": record.get("number"),
                "title": record.get("title"),
                "path": rel.as_posix(),
                "sha256": sha256_path(path),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "source_file": metadata.get("source_file"),
        "source_format": metadata.get("source_format"),
        "source_sha256": sha256_path(source),
        "chapter_count": len(extracted),
        "extracted": extracted,
    }


def verify_manifest(book_dir: Path, metadata: dict, progress: dict, data: dict) -> tuple[str, int]:
    try:
        data = parse_document(SchemaKind.SOURCE_MANIFEST, data).data
    except SchemaError as exc:
        raise book.BookError(f"Invalid source-manifest.json: {exc}") from exc

    if data.get("source_file") != metadata.get("source_file"):
        raise book.BookError("source-manifest.json source_file disagrees with metadata.json")
    if data.get("source_format") != metadata.get("source_format"):
        raise book.BookError("source-manifest.json source_format disagrees with metadata.json")

    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise book.BookError("progress.json must contain a chapters array")
    items = data.get("extracted")
    if not isinstance(items, list):
        raise book.BookError("source-manifest.json extracted must be an array")
    if data.get("chapter_count") != len(chapters) or len(items) != len(chapters):
        raise book.BookError(
            "source-manifest.json chapter_count/extracted entries disagree with progress.json"
        )

    expected_source_sha = normalized_expected_sha(
        data.get("source_sha256") if isinstance(data.get("source_sha256"), str) else None,
        "source-manifest.json source_sha256",
    )
    if not expected_source_sha:
        raise book.BookError("source-manifest.json source_sha256 is missing")

    source = source_file_path(book_dir, metadata)
    if not source.is_file():
        raise book.BookError(f"Preserved source is missing: {source.relative_to(book_dir).as_posix()}")
    actual_source_sha = sha256_path(source)
    if actual_source_sha != expected_source_sha:
        raise book.BookError(
            f"Preserved source hash mismatch: expected {expected_source_sha}, got {actual_source_sha}"
        )

    for record, item in zip(chapters, items):
        if not isinstance(record, dict):
            raise book.BookError("Every chapter entry must be an object")
        if not isinstance(item, dict):
            raise book.BookError("Every source-manifest.json extracted entry must be an object")

        rel = checked_source_rel(book_dir, record.get("source_path"))
        rel_text = rel.as_posix()
        if item.get("path") != rel_text:
            raise book.BookError(
                f"Manifest path mismatch for chapter {record.get('number')}: expected {rel_text}, got {item.get('path')!r}"
            )
        if item.get("number") != record.get("number"):
            raise book.BookError(f"Manifest chapter number mismatch for {rel_text}")
        if item.get("title") != record.get("title"):
            raise book.BookError(f"Manifest chapter title mismatch for {rel_text}")

        expected_sha = normalized_expected_sha(
            item.get("sha256") if isinstance(item.get("sha256"), str) else None,
            f"manifest hash for {rel_text}",
        )
        if not expected_sha:
            raise book.BookError(f"Manifest hash is missing for {rel_text}")

        path = book_dir / rel
        if not path.is_file():
            raise book.BookError(f"Extracted artifact is missing: {rel_text}")
        actual_sha = sha256_path(path)
        if actual_sha != expected_sha:
            raise book.BookError(
                f"Extracted artifact hash mismatch for {rel_text}: expected {expected_sha}, got {actual_sha}"
            )

    return expected_source_sha, len(items)


def seal_command(args: argparse.Namespace) -> int:
    errors, _ = book.validate_book(args.slug)
    if errors:
        raise book.BookError("Cannot seal invalid book structure:\n- " + "\n- ".join(errors))

    book_dir, metadata, progress = book.load_book(args.slug)
    source = source_file_path(book_dir, metadata)
    data = build_manifest(book_dir, metadata, progress, source)
    write_source_manifest(book_dir, data)
    print(
        f"Sealed source corpus for books/{args.slug}: "
        f"{data['chapter_count']} extracted artifact(s), SHA-256 {data['source_sha256']}"
    )
    return 0


def verify_command(args: argparse.Namespace) -> int:
    errors, _ = book.validate_book(args.slug)
    if errors:
        raise book.BookError("Cannot verify invalid book structure:\n- " + "\n- ".join(errors))

    book_dir, metadata, progress = book.load_book(args.slug)
    data = load_source_manifest(book_dir)
    if data is None:
        raise book.BookError("source-manifest.json is missing; seal the corpus before verification")

    source_sha, chapter_count = verify_manifest(book_dir, metadata, progress, data)
    print(
        f"Verified source corpus for books/{args.slug}: "
        f"{chapter_count} extracted artifact(s), SHA-256 {source_sha}"
    )
    return 0


def restore_command(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise book.BookError(f"Source file does not exist: {source}")

    book_dir, metadata, progress = book.load_book(args.slug)
    source_format = book.detect_format(source)
    expected_format = metadata.get("source_format")
    if expected_format and source_format != expected_format:
        raise book.BookError(
            f"Source format mismatch: supplied {source_format}, metadata expects {expected_format}"
        )

    existing_document = load_source_manifest_document(book_dir)
    existing_manifest = existing_document.data if existing_document is not None else None
    manifest_sha = normalized_expected_sha(
        str(existing_manifest.get("source_sha256")) if existing_manifest and existing_manifest.get("source_sha256") else None,
        "source-manifest.json source_sha256",
    )
    argument_sha = normalized_expected_sha(args.expected_sha256, "--expected-sha256")
    if manifest_sha and argument_sha and manifest_sha != argument_sha:
        raise book.BookError(
            "--expected-sha256 disagrees with source-manifest.json; refusing to choose between source identities"
        )
    expected_sha = manifest_sha or argument_sha
    if not expected_sha:
        raise book.BookError(
            "No trusted source SHA-256 is recorded. Supply --expected-sha256 from durable provenance before restoring."
        )

    actual_sha = sha256_path(source)
    if actual_sha != expected_sha:
        raise book.BookError(f"SHA-256 mismatch: expected {expected_sha}, got {actual_sha}")

    extracted_chapters, _ = book.extract_chapters(source, source_format)
    records = progress.get("chapters")
    if not isinstance(records, list):
        raise book.BookError("progress.json must contain a chapters array")
    if len(extracted_chapters) != len(records):
        raise book.BookError(
            f"Source chapter count mismatch: source yields {len(extracted_chapters)}, progress expects {len(records)}"
        )

    recorded_hashes: dict[str, str] = {}
    if existing_manifest:
        items = existing_manifest.get("extracted")
        if not isinstance(items, list):
            raise book.BookError("source-manifest.json extracted must be an array")
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                recorded_hashes[item["path"]] = normalized_expected_sha(item["sha256"], f"manifest hash for {item['path']}") or ""

    with tempfile.TemporaryDirectory(prefix=".corpus-restore-", dir=book_dir) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        staged_extracted = temp_dir / "extracted"
        staged_extracted.mkdir(parents=True)
        manifest_items: list[dict] = []

        for record, chapter in zip(records, extracted_chapters):
            if not isinstance(record, dict):
                raise book.BookError("Every chapter entry must be an object")
            expected_title = record.get("title")
            if expected_title != chapter.title:
                raise book.BookError(
                    f"Chapter {record.get('number')} title mismatch: progress has {expected_title!r}, source has {chapter.title!r}"
                )
            rel = checked_source_rel(book_dir, record.get("source_path"))
            relative_inside_extracted = Path(*rel.parts[1:])
            staged_path = staged_extracted / relative_inside_extracted
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_text(chapter.content, encoding="utf-8")
            artifact_sha = sha256_path(staged_path)
            recorded = recorded_hashes.get(rel.as_posix())
            if recorded and artifact_sha != recorded:
                raise book.BookError(
                    f"Extracted artifact hash mismatch for {rel.as_posix()}: expected {recorded}, got {artifact_sha}"
                )
            manifest_items.append(
                {
                    "number": record.get("number"),
                    "title": record.get("title"),
                    "path": rel.as_posix(),
                    "sha256": artifact_sha,
                }
            )

        new_manifest = {
            "schema_version": SCHEMA_VERSION,
            "source_file": metadata.get("source_file"),
            "source_format": source_format,
            "source_sha256": actual_sha,
            "chapter_count": len(manifest_items),
            "extracted": manifest_items,
        }
        try:
            new_manifest = parse_document(SchemaKind.SOURCE_MANIFEST, new_manifest).data
        except SchemaError as exc:
            raise book.BookError(f"Cannot construct valid source-manifest.json: {exc}") from exc

        stored_source = source_file_path(book_dir, metadata)
        stored_source.parent.mkdir(parents=True, exist_ok=True)
        staged_source = stored_source.with_name(f".{stored_source.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(source, staged_source)

        extracted_dir = book_dir / "extracted"
        backup_dir = book_dir / f".extracted-backup-{uuid.uuid4().hex}"
        had_existing = extracted_dir.exists()
        if had_existing:
            os.replace(extracted_dir, backup_dir)
        try:
            os.replace(staged_extracted, extracted_dir)
            os.replace(staged_source, stored_source)
            write_source_manifest(
                book_dir,
                new_manifest,
                expected_version=existing_document.version if existing_document is not None else None,
                create_only=existing_document is None,
            )
        except Exception:
            if extracted_dir.exists():
                shutil.rmtree(extracted_dir, ignore_errors=True)
            if had_existing and backup_dir.exists():
                os.replace(backup_dir, extracted_dir)
            if staged_source.exists():
                staged_source.unlink()
            raise
        else:
            if backup_dir.exists():
                shutil.rmtree(backup_dir)

    errors, _ = book.validate_book(args.slug)
    if errors:
        raise book.BookError("Restored corpus but book validation still fails:\n- " + "\n- ".join(errors))
    verify_manifest(book_dir, metadata, progress, new_manifest)

    print(
        f"Restored complete source corpus for books/{args.slug}: "
        f"{len(records)} extracted artifact(s), SHA-256 {actual_sha}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Book Translator source-corpus integrity helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seal = subparsers.add_parser(
        "seal",
        help="Record SHA-256 identity for the preserved source and every extracted artifact.",
    )
    seal.add_argument("slug", help="Book slug under books/.")
    seal.set_defaults(func=seal_command)

    verify = subparsers.add_parser(
        "verify",
        help="Verify the preserved source and every extracted artifact against source-manifest.json.",
    )
    verify.add_argument("slug", help="Book slug under books/.")
    verify.set_defaults(func=verify_command)

    restore = subparsers.add_parser(
        "restore",
        help="Rebuild the complete extracted corpus from one verified source file.",
    )
    restore.add_argument("slug", help="Book slug under books/.")
    restore.add_argument("source", help="Path to the original source file.")
    restore.add_argument(
        "--expected-sha256",
        help="Trusted SHA-256 for legacy workspaces without source-manifest.json.",
    )
    restore.set_defaults(func=restore_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except book.BookError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
