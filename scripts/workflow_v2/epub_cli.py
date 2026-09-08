"""CLI/filesystem adapter for deterministic Workflow v2 EPUB delivery."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .epub_output import (
    EpubOutputError,
    OUTPUT_MANIFEST_PATH,
    build_epub_bytes,
    build_input_snapshot,
    build_output_manifest,
    input_fingerprint,
    resolve_output_status,
    validate_epub_bytes,
    validate_output_manifest,
)
from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .reviews import ReviewError, ReviewLedgerManager
from .schemas import SchemaError, SchemaKind
from .status_cli import default_preflight
from .storage import StorageAlreadyExists, StorageError, StorageNotFound, StorageVersionConflict


class EpubCliError(RuntimeError):
    """Expected EPUB build/status error suitable for concise CLI output."""


ErrorFactory = Callable[[str], Exception]
_COVER_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise EpubCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise EpubCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise EpubCliError(f"Book directory does not exist: books/{slug}")
    return book_dir


def _artifact_reader(book_dir: Path):
    root = book_dir.resolve(strict=False)

    def read(relative_path: str) -> bytes:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise OSError("artifact path must be a non-empty relative path")
        target = (root / relative_path).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise OSError(f"artifact path escapes book workspace: {relative_path}") from exc
        return target.read_bytes()

    return read


def _load_state(repository: WorkflowStateRepository):
    try:
        metadata = repository.read("metadata.json", SchemaKind.METADATA, allow_legacy=True)
        progress = repository.read("progress.json", SchemaKind.PROGRESS, allow_legacy=True)
        ledger = repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER)
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise EpubCliError(f"workflow state is unavailable or invalid: {exc}") from exc
    return metadata, progress, ledger


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _safe_output_name(slug: str, output: str | None) -> str:
    name = output or f"{slug}.epub"
    if not isinstance(name, str) or not name.strip():
        raise EpubCliError("EPUB output filename must be non-empty")
    parsed = PurePosixPath(name)
    if parsed.is_absolute() or len(parsed.parts) != 1 or name in {".", ".."} or "\\" in name:
        raise EpubCliError("EPUB output must be one filename under output/")
    if parsed.suffix.lower() != ".epub":
        raise EpubCliError("EPUB output filename must end with .epub")
    return name


def _canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_if_changed(storage: FilesystemStorage, path: str, content: bytes) -> str:
    try:
        current = storage.read(path)
    except StorageNotFound:
        try:
            storage.create_if_absent(path, content)
        except StorageAlreadyExists as exc:
            raise EpubCliError(f"output changed concurrently: {path}") from exc
        except StorageError as exc:
            raise EpubCliError(f"cannot create output {path}: {exc}") from exc
        return "created"
    if current.content == content:
        return "unchanged"
    try:
        storage.write_if_version(path, content, current.version)
    except StorageVersionConflict as exc:
        raise EpubCliError(f"output changed concurrently: {path}") from exc
    except StorageError as exc:
        raise EpubCliError(f"cannot update output {path}: {exc}") from exc
    return "updated"


def _read_manifest(storage: FilesystemStorage) -> tuple[dict[str, Any] | None, str | None]:
    try:
        stored = storage.read(OUTPUT_MANIFEST_PATH)
    except StorageNotFound:
        return None, None
    except StorageError as exc:
        raise EpubCliError(f"cannot read {OUTPUT_MANIFEST_PATH}: {exc}") from exc
    try:
        text = stored.content.decode("utf-8")
        parsed = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid output manifest JSON: {exc}"
    try:
        return validate_output_manifest(parsed), None
    except EpubOutputError as exc:
        return None, str(exc)


def _resolutions(book_dir: Path, repository: WorkflowStateRepository, progress, metadata):
    manager = ReviewLedgerManager(repository, artifact_reader=_artifact_reader(book_dir))
    try:
        return manager.resolve_all(progress, metadata)
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise EpubCliError(f"review evidence is unavailable or invalid: {exc}") from exc


def _build_identity(
    root: Path,
    slug: str,
    *,
    preview: bool,
    strict: bool,
):
    book_dir = _book_directory(root, slug)
    storage = FilesystemStorage(book_dir)
    repository = WorkflowStateRepository(storage)
    metadata_doc, progress_doc, ledger_doc = _load_state(repository)
    structural_errors, corpus = default_preflight(root, slug)
    if not isinstance(corpus, Mapping) or corpus.get("state") != "verified":
        detail = corpus.get("error") if isinstance(corpus, Mapping) else None
        raise EpubCliError(f"source corpus must be verified{': ' + str(detail) if detail else ''}")
    if strict and structural_errors:
        raise EpubCliError("structural validation failed: " + "; ".join(str(item) for item in structural_errors))

    chapters = progress_doc.data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise EpubCliError("EPUB build requires at least one chapter")
    if preview:
        invalid = [
            chapter.get("number")
            for chapter in chapters
            if not isinstance(chapter, Mapping) or chapter.get("status") not in {"translated", "reviewed"}
        ]
        if invalid:
            raise EpubCliError(
                "preview EPUB requires translated/reviewed chapters: " + ", ".join(map(str, invalid))
            )
        resolutions = []
    else:
        invalid = [
            chapter.get("number")
            for chapter in chapters
            if not isinstance(chapter, Mapping) or chapter.get("status") != "reviewed"
        ]
        if invalid and strict:
            raise EpubCliError("final EPUB requires reviewed chapters: " + ", ".join(map(str, invalid)))
        resolutions = _resolutions(book_dir, repository, progress_doc.data, metadata_doc.data)
        if strict:
            not_pass = [item for item in resolutions if item.state != "pass"]
            if not_pass:
                raise EpubCliError(
                    "final EPUB requires current PASS review evidence: "
                    + ", ".join(f"{item.unit_id}={item.state}" for item in not_pass)
                )

    reader = _artifact_reader(book_dir)
    try:
        snapshot = build_input_snapshot(
            metadata_doc.data,
            progress_doc.data,
            resolutions,
            reader,
            preview=preview,
            cover_reader=reader,
        )
        fingerprint = input_fingerprint(snapshot)
    except EpubOutputError as exc:
        raise EpubCliError(str(exc)) from exc
    return {
        "book_dir": book_dir,
        "storage": storage,
        "repository": repository,
        "metadata": metadata_doc,
        "progress": progress_doc,
        "ledger": ledger_doc,
        "structural_errors": list(structural_errors),
        "corpus": dict(corpus),
        "resolutions": resolutions,
        "snapshot": snapshot,
        "fingerprint": fingerprint,
        "reader": reader,
    }


def _unit_payloads(context) -> list[dict[str, Any]]:
    chapters = context["progress"].data["chapters"]
    reader = context["reader"]
    units = []
    for chapter in chapters:
        path = chapter["translation_path"]
        try:
            markdown = reader(path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EpubCliError(f"translation is not valid UTF-8: {path}") from exc
        units.append(
            {
                "number": chapter["number"],
                "title": chapter["title"],
                "slug": chapter["slug"],
                "markdown": markdown,
            }
        )
    return units


def _cover_payload(context):
    path = context["metadata"].data.get("cover_path")
    if path is None:
        return None
    suffix = PurePosixPath(path).suffix.lower()
    media_type = _COVER_MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise EpubCliError(f"unsupported cover extension: {suffix or '<none>'}")
    try:
        content = context["reader"](path)
    except OSError as exc:
        raise EpubCliError(f"cannot read cover: {path}: {exc}") from exc
    return {"name": PurePosixPath(path).name, "media_type": media_type, "content": content}


def _stored_output_status(context, *, requested_artifact_path: str | None = None) -> dict[str, Any]:
    storage = context["storage"]
    manifest, manifest_error = _read_manifest(storage)
    if manifest_error is not None:
        return {"state": "invalid", "reason": manifest_error}
    if manifest is None:
        return {"state": "missing"}
    if requested_artifact_path is not None and manifest["artifact_path"] != requested_artifact_path:
        return {"state": "stale", "artifact_path": manifest["artifact_path"]}
    try:
        stored_artifact = storage.read(manifest["artifact_path"])
    except StorageNotFound:
        return {"state": "missing", "artifact_path": manifest["artifact_path"]}
    except StorageError as exc:
        return {"state": "invalid", "reason": str(exc)}
    try:
        validate_epub_bytes(stored_artifact.content, expected_unit_count=len(context["progress"].data["chapters"]))
    except EpubOutputError as exc:
        return {"state": "invalid", "reason": str(exc), "artifact_path": manifest["artifact_path"]}
    status = resolve_output_status(
        manifest,
        artifact_bytes=stored_artifact.content,
        current_fingerprint=context["fingerprint"],
        expected_unit_count=len(context["progress"].data["chapters"]),
    )
    if context["structural_errors"] and status["state"] == "current":
        return {
            "state": "invalid",
            "reason": "structural validation failed: " + "; ".join(str(item) for item in context["structural_errors"]),
            "artifact_path": manifest["artifact_path"],
        }
    return status


def epub_build_command(args: argparse.Namespace, root: Path) -> int:
    preview = bool(args.allow_unreviewed)
    output_name = _safe_output_name(args.slug, args.output)
    artifact_path = f"output/{output_name}"
    context = _build_identity(root, args.slug, preview=preview, strict=True)
    existing = _stored_output_status(context, requested_artifact_path=artifact_path)
    if existing.get("state") == "current":
        print(f"Built EPUB unchanged: books/{args.slug}/{artifact_path}")
        return 0

    metadata = context["metadata"].data
    try:
        artifact_bytes = build_epub_bytes(
            book_slug=args.slug,
            title=metadata["title"],
            author=metadata.get("author"),
            language=metadata["target_language"],
            units=_unit_payloads(context),
            fingerprint=context["fingerprint"],
            cover=_cover_payload(context),
        )
        validate_epub_bytes(
            artifact_bytes,
            expected_unit_count=len(context["progress"].data["chapters"]),
        )
    except EpubOutputError as exc:
        raise EpubCliError(str(exc)) from exc

    artifact_disposition = _write_if_changed(context["storage"], artifact_path, artifact_bytes)
    try:
        persisted = context["storage"].read(artifact_path)
        validate_epub_bytes(persisted.content, expected_unit_count=len(context["progress"].data["chapters"]))
    except (StorageError, EpubOutputError) as exc:
        raise EpubCliError(f"persisted EPUB failed validation: {exc}") from exc
    if hashlib.sha256(persisted.content).hexdigest() != hashlib.sha256(artifact_bytes).hexdigest():
        raise EpubCliError("persisted EPUB bytes changed after atomic write")

    manifest = build_output_manifest(
        book_slug=args.slug,
        preview=preview,
        artifact_path=artifact_path,
        artifact_sha256=hashlib.sha256(persisted.content).hexdigest(),
        unit_count=len(context["progress"].data["chapters"]),
        input_fingerprint=context["fingerprint"],
        repository_commit=_git_head(root),
        state_revisions={
            "metadata": context["metadata"].version,
            "progress": context["progress"].version,
            "review_ledger": context["ledger"].version,
        },
    )
    manifest_disposition = _write_if_changed(
        context["storage"], OUTPUT_MANIFEST_PATH, _canonical_manifest_bytes(manifest)
    )
    final_status = _stored_output_status(context, requested_artifact_path=artifact_path)
    if final_status.get("state") != "current":
        raise EpubCliError(f"EPUB output is not current after build: {final_status}")
    print(
        f"Built EPUB: books/{args.slug}/{artifact_path} "
        f"artifact={artifact_disposition} manifest={manifest_disposition} units={manifest['unit_count']}"
    )
    return 0


def build_status_command(args: argparse.Namespace, root: Path) -> int:
    preview = bool(args.allow_unreviewed)
    try:
        context = _build_identity(root, args.slug, preview=preview, strict=False)
        status = _stored_output_status(context)
    except EpubCliError as exc:
        status = {"state": "invalid", "reason": str(exc)}
    if args.json:
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    else:
        detail = f" artifact={status['artifact_path']}" if status.get("artifact_path") else ""
        reason = f" reason={status['reason']}" if status.get("reason") else ""
        print(f"build={status['state']}{detail}{reason}")
    return 0 if status["state"] in {"current", "missing", "stale"} else 1


def _adapt(command: Callable[[argparse.Namespace], int], error_factory: ErrorFactory):
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except EpubCliError as exc:
            raise error_factory(str(exc)) from exc

    return run


def register_epub_commands(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    error_factory: ErrorFactory = EpubCliError,
) -> None:
    """Extend the existing build parser and register read-only build-status."""

    build = subparsers.choices.get("build")
    if build is None:
        raise EpubCliError("existing build command is unavailable")
    has_format = any("--format" in action.option_strings for action in build._actions)
    if not has_format:
        original = build.get_default("func")
        if original is None:
            raise EpubCliError("existing Markdown build handler is unavailable")
        build.add_argument(
            "--format",
            choices=("markdown", "epub"),
            default="markdown",
            help="Output format. Defaults to markdown.",
        )

        def dispatch(args: argparse.Namespace) -> int:
            if args.format == "epub":
                return _adapt(lambda value: epub_build_command(value, root), error_factory)(args)
            return original(args)

        build.set_defaults(func=dispatch)

    if "build-status" not in subparsers.choices:
        status = subparsers.add_parser(
            "build-status",
            help="Report whether generated EPUB output matches current relevant inputs.",
        )
        status.add_argument("slug", help="Book slug under books/.")
        status.add_argument("--format", choices=("epub",), default="epub")
        status.add_argument(
            "--allow-unreviewed",
            action="store_true",
            help="Resolve preview EPUB identity instead of final reviewed identity.",
        )
        status.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
        status.set_defaults(
            func=_adapt(lambda args: build_status_command(args, root), error_factory)
        )
