"""Explicit Workflow v2 source identity and book CLI integration."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .repository import RepositoryError
from .schemas import SchemaError, SchemaKind
from .source_integrity import SourceIntegrityError, build_source_manifest, sha256_path
from .storage import StorageError


class SourceCliError(RuntimeError):
    """Expected explicit-source workflow error suitable for CLI output."""


ErrorFactory = Callable[[str], Exception]


def _explicit_source(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("source")
    return value if isinstance(value, Mapping) else None


def source_storage_mode(metadata: Mapping[str, Any]) -> str | None:
    source = _explicit_source(metadata)
    if source is None:
        return None
    value = source.get("storage_mode")
    return value if isinstance(value, str) else None


def normalize_structural_errors(
    book_dir: Path,
    metadata: Mapping[str, Any],
    errors: Sequence[str],
) -> list[str]:
    """Apply explicit-source structural policy without duplicating hash verification."""

    result = [str(error) for error in errors]
    source = _explicit_source(metadata)
    if source is None:
        return result

    source_file = metadata.get("source_file")
    legacy_missing = f"Source file declared in metadata.json does not exist: source/{source_file}"
    if source.get("storage_mode") == "private_external":
        result = [error for error in result if error != legacy_missing]

    if not (book_dir / "source-manifest.json").is_file():
        message = "Missing source-manifest.json for explicit-source book"
        if message not in result:
            result.append(message)
    return result


def manifest_structure_errors(
    book_dir: Path,
    metadata: Mapping[str, Any],
    repository: Any,
) -> list[str]:
    source = _explicit_source(metadata)
    if source is None:
        return []
    if not (book_dir / "source-manifest.json").is_file():
        return []

    try:
        manifest = repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST).data
    except (SchemaError, RepositoryError, StorageError) as exc:
        return [f"Invalid source-manifest.json: {exc}"]

    expected = {
        "source_file": metadata.get("source_file"),
        "source_format": metadata.get("source_format"),
        "source_storage_mode": source.get("storage_mode"),
        "source_size_bytes": source.get("size_bytes"),
        "source_sha256": source.get("sha256"),
    }
    errors: list[str] = []
    for key, value in expected.items():
        if manifest.get(key) != value:
            errors.append(f"source-manifest.json {key} disagrees with metadata.json")
    return errors


def _active_book_module():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "slugify") and hasattr(main, "state_repository"):
        return main
    return importlib.import_module("book")


def _source_identity(source: Path, *, private: bool) -> dict[str, Any]:
    return {
        "storage_mode": "private_external" if private else "embedded",
        "filename": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": sha256_path(source),
    }


def source_extract_command(
    args: argparse.Namespace,
    root: Path,
    original: Callable[[argparse.Namespace], int],
) -> int:
    book_module = _active_book_module()
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        return original(args)

    identity = _source_identity(source, private=bool(args.private_source))
    slug = book_module.slugify(args.slug or source.stem)
    book_dir = root / "books" / slug
    existed_before = book_dir.exists()
    output = io.StringIO()

    try:
        with contextlib.redirect_stdout(output):
            result = original(args)

        repository = book_module.state_repository(book_dir)
        metadata_doc = repository.read("metadata.json", SchemaKind.METADATA, allow_legacy=True)
        progress_doc = repository.read("progress.json", SchemaKind.PROGRESS, allow_legacy=True)
        metadata = dict(metadata_doc.data)
        metadata["source"] = identity
        repository.write_if_version(
            "metadata.json",
            SchemaKind.METADATA,
            metadata,
            metadata_doc.version,
        )

        stored_source = book_dir / "source" / identity["filename"]
        manifest = build_source_manifest(book_dir, metadata, progress_doc.data, stored_source)
        repository.create("source-manifest.json", SchemaKind.SOURCE_MANIFEST, manifest)

        if identity["storage_mode"] == "private_external" and stored_source.is_file():
            stored_source.unlink()

        errors, _ = book_module.validate_book(slug)
        errors = normalize_structural_errors(book_dir, metadata, errors)
        errors.extend(manifest_structure_errors(book_dir, metadata, repository))
        if errors:
            raise SourceCliError(
                "Explicit-source initialization failed validation:\n- " + "\n- ".join(errors)
            )
    except (SourceIntegrityError, SchemaError, RepositoryError, StorageError) as exc:
        if not existed_before and book_dir.exists():
            shutil.rmtree(book_dir, ignore_errors=True)
        raise SourceCliError(str(exc)) from exc
    except Exception as exc:
        if not existed_before and book_dir.exists():
            shutil.rmtree(book_dir, ignore_errors=True)
        if isinstance(exc, SourceCliError):
            raise
        raise SourceCliError(str(exc)) from exc

    print(output.getvalue(), end="")
    return result


def source_validate_command(args: argparse.Namespace, root: Path) -> int:
    book_module = _active_book_module()
    errors, warnings = book_module.validate_book(args.slug)
    try:
        book_dir, metadata, _ = book_module.load_book(args.slug)
    except Exception:
        book_dir = None
        metadata = None
    if book_dir is not None and isinstance(metadata, Mapping):
        errors = normalize_structural_errors(book_dir, metadata, errors)
        errors.extend(
            manifest_structure_errors(book_dir, metadata, book_module.state_repository(book_dir))
        )

    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"books/{args.slug}: valid")
    return 0


def _adapt_errors(command: Callable[[argparse.Namespace], int], error_factory: ErrorFactory):
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except SourceCliError as exc:
            raise error_factory(str(exc)) from exc

    return run


def register_source_overrides(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    error_factory: ErrorFactory,
) -> None:
    """Extend existing book.py extract/validate parsers without duplicating them."""

    extract = subparsers.choices.get("extract")
    validate = subparsers.choices.get("validate")
    if extract is None or validate is None:
        raise SourceCliError("book.py extract/validate parsers are unavailable")
    if getattr(extract, "_explicit_source_v1_registered", False):
        return

    original_extract = extract.get_default("func")
    extract.add_argument(
        "--private-source",
        action="store_true",
        help="Record source identity but do not retain the original binary in the book workspace.",
    )
    extract.set_defaults(
        func=_adapt_errors(
            lambda args: source_extract_command(args, root, original_extract),
            error_factory,
        )
    )
    setattr(extract, "_explicit_source_v1_registered", True)

    validate.set_defaults(
        func=_adapt_errors(
            lambda args: source_validate_command(args, root),
            error_factory,
        )
    )
