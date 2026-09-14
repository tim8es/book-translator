"""Explicit source identity and current-workspace CLI integration."""

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


CURRENT_REVIEW_EVIDENCE = "review-ledger-v1"


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
    """Apply the current workspace contract without duplicating hash verification."""

    result = [str(error) for error in errors]
    source = _explicit_source(metadata)
    if source is None:
        message = "metadata.json source identity is required for the current workflow"
        if message not in result:
            result.append(message)
    else:
        source_file = metadata.get("source_file")
        missing_source = f"Source file declared in metadata.json does not exist: source/{source_file}"
        if source.get("storage_mode") == "private_external":
            result = [error for error in result if error != missing_source]

        if not (book_dir / "source-manifest.json").is_file():
            message = "Missing source-manifest.json for current workflow book"
            if message not in result:
                result.append(message)

    workflow = metadata.get("workflow")
    if not isinstance(workflow, Mapping) or workflow.get("review_evidence") != CURRENT_REVIEW_EVIDENCE:
        message = (
            "metadata.json workflow.review_evidence must equal "
            f"{CURRENT_REVIEW_EVIDENCE!r} for the current workflow"
        )
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


def manifest_integrity_errors(
    book_dir: Path,
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
    repository: Any,
) -> list[str]:
    """Verify the sealed current corpus before validate/build admission."""

    source = _explicit_source(metadata)
    if source is None or not (book_dir / "source-manifest.json").is_file():
        return []
    try:
        manifest = repository.read("source-manifest.json", SchemaKind.SOURCE_MANIFEST).data
    except (SchemaError, RepositoryError, StorageError) as exc:
        return [f"Invalid source-manifest.json: {exc}"]

    errors: list[str] = []
    source_file = metadata.get("source_file")
    source_path = book_dir / "source" / str(source_file)
    storage_mode = source.get("storage_mode")
    if source_path.is_file():
        expected_size = source.get("size_bytes")
        if source_path.stat().st_size != expected_size:
            errors.append(
                f"Preserved source size mismatch: expected {expected_size}, got {source_path.stat().st_size}"
            )
        actual_source_sha = sha256_path(source_path)
        expected_source_sha = manifest.get("source_sha256")
        if actual_source_sha != expected_source_sha:
            errors.append(
                f"Preserved source hash mismatch: expected {expected_source_sha}, got {actual_source_sha}"
            )
    elif storage_mode == "embedded":
        errors.append(f"Preserved source is missing: source/{source_file}")

    chapters = progress.get("chapters")
    items = manifest.get("extracted")
    if not isinstance(chapters, list) or not isinstance(items, list):
        return errors
    if manifest.get("chapter_count") != len(chapters) or len(items) != len(chapters):
        errors.append("source-manifest.json chapter_count/extracted entries disagree with progress.json")
        return errors

    for chapter, item in zip(chapters, items):
        if not isinstance(chapter, Mapping) or not isinstance(item, Mapping):
            errors.append("source-manifest.json extracted entries must match progress chapter objects")
            continue
        source_rel = chapter.get("source_path")
        if item.get("path") != source_rel:
            errors.append(
                f"Manifest path mismatch for chapter {chapter.get('number')}: expected {source_rel}, got {item.get('path')!r}"
            )
            continue
        if item.get("number") != chapter.get("number"):
            errors.append(f"Manifest chapter number mismatch for {source_rel}")
        if item.get("title") != chapter.get("title"):
            errors.append(f"Manifest chapter title mismatch for {source_rel}")
        if not isinstance(source_rel, str):
            errors.append(f"Invalid extracted source path in progress.json: {source_rel!r}")
            continue
        rel = Path(source_rel)
        if rel.is_absolute() or ".." in rel.parts:
            errors.append(f"Source path escapes book workspace: {source_rel}")
            continue
        path = book_dir / rel
        if not path.is_file():
            errors.append(f"Extracted artifact is missing: {source_rel}")
            continue
        actual_sha = sha256_path(path)
        expected_sha = item.get("sha256")
        if actual_sha != expected_sha:
            errors.append(
                f"Extracted artifact hash mismatch for {source_rel}: expected {expected_sha}, got {actual_sha}"
            )
    return errors


def translation_acceptance_errors(
    metadata: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> list[str]:
    """Reject translated-or-later state that bypassed Translator acceptance."""

    workflow = metadata.get("workflow")
    workflow_revision = None
    if isinstance(workflow, Mapping):
        for key in ("resolved_revision", "requested_ref"):
            value = workflow.get(key)
            if isinstance(value, str) and value.strip():
                workflow_revision = value
                break

    errors: list[str] = []
    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        return errors
    for chapter in chapters:
        if not isinstance(chapter, Mapping) or chapter.get("status") not in {"translated", "reviewed"}:
            continue
        number = chapter.get("number")
        evidence = chapter.get("translation_acceptance")
        if not isinstance(evidence, Mapping):
            errors.append(
                f"Chapter {number}: status={chapter.get('status')} requires current translation_acceptance evidence"
            )
            continue
        expected_unit = f"chapter-{int(number):06d}" if type(number) is int and number > 0 else None
        if expected_unit is not None and evidence.get("unit_id") != expected_unit:
            errors.append(f"Chapter {number}: translation_acceptance unit identity is invalid")
        if evidence.get("role") != "translator":
            errors.append(f"Chapter {number}: translation_acceptance role must be translator")
        if workflow_revision is not None and evidence.get("workflow_revision") != workflow_revision:
            errors.append(
                f"Chapter {number}: translation_acceptance uses another workflow revision"
            )
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


def _current_workspace_errors(book_module: Any, slug: str) -> list[str]:
    try:
        book_dir, metadata, progress = book_module.load_book(slug)
    except Exception as exc:
        return [str(exc)]

    errors, _ = book_module.validate_book(slug)
    errors = normalize_structural_errors(book_dir, metadata, errors)
    repository = book_module.state_repository(book_dir)
    errors.extend(manifest_structure_errors(book_dir, metadata, repository))
    errors.extend(manifest_integrity_errors(book_dir, metadata, progress, repository))
    errors.extend(translation_acceptance_errors(metadata, progress))
    return errors


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

        errors = _current_workspace_errors(book_module, slug)
        if errors:
            raise SourceCliError(
                "Current workflow initialization failed validation:\n- " + "\n- ".join(errors)
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
    errors = _current_workspace_errors(book_module, args.slug)
    try:
        _, warnings = book_module.validate_book(args.slug)
    except Exception:
        warnings = []

    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"books/{args.slug}: valid")
    return 0


def source_build_command(
    args: argparse.Namespace,
    root: Path,
    original: Callable[[argparse.Namespace], int],
) -> int:
    book_module = _active_book_module()
    errors = _current_workspace_errors(book_module, args.slug)
    if errors:
        raise SourceCliError(
            "Book does not satisfy the current workflow contract:\n- " + "\n- ".join(errors)
        )
    return original(args)


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
    """Extend extract/validate/build with the current workspace contract."""

    extract = subparsers.choices.get("extract")
    validate = subparsers.choices.get("validate")
    build = subparsers.choices.get("build")
    if extract is None or validate is None or build is None:
        raise SourceCliError("book.py extract/validate/build parsers are unavailable")
    if getattr(extract, "_explicit_source_v1_registered", False):
        return

    original_extract = extract.get_default("func")
    original_build = build.get_default("func")
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
    build.set_defaults(
        func=_adapt_errors(
            lambda args: source_build_command(args, root, original_build),
            error_factory,
        )
    )
