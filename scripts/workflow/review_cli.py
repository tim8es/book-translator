"""Argparse integration for current machine review evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .claims import unit_id_for_chapter
from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .review_report import build_review_report_snapshot, render_review_report_markdown
from .reviews import (
    AcceptReviewResult,
    ReviewError,
    ReviewLedgerManager,
    ReviewRecordResult,
    ReviewResolution,
)
from .schemas import SchemaError, SchemaKind
from .status_cli import register_status_commands
from .storage import (
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
)
from .translations import (
    AcceptTranslationResult,
    TranslationAcceptanceError,
    TranslationAcceptanceManager,
)


REVIEW_REPORT_PATH = "REVIEW_REPORT.md"


class ReviewCliError(RuntimeError):
    """Expected review-command error suitable for Book Translator CLI output."""


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise ReviewCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise ReviewCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise ReviewCliError(f"Book directory does not exist: books/{slug}")
    return book_dir


def _repository(root: Path, slug: str) -> tuple[Path, WorkflowStateRepository]:
    book_dir = _book_directory(root, slug)
    return book_dir, WorkflowStateRepository(FilesystemStorage(book_dir))


def _load_progress(repository: WorkflowStateRepository) -> tuple[dict[str, Any], str]:
    try:
        loaded = repository.read("progress.json", SchemaKind.PROGRESS)
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(f"Invalid progress.json: {exc}") from exc
    return loaded.data, loaded.version


def _load_metadata(repository: WorkflowStateRepository) -> dict[str, Any]:
    try:
        return repository.read("metadata.json", SchemaKind.METADATA).data
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(f"Invalid metadata.json: {exc}") from exc


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


def _manager(book_dir: Path, repository: WorkflowStateRepository) -> ReviewLedgerManager:
    return ReviewLedgerManager(repository, artifact_reader=_artifact_reader(book_dir))


def _translation_manager(
    book_dir: Path,
    repository: WorkflowStateRepository,
) -> TranslationAcceptanceManager:
    return TranslationAcceptanceManager(
        repository,
        artifact_reader=_artifact_reader(book_dir),
    )


def _require_current_translation_acceptance(
    progress: Mapping[str, Any],
    metadata: Mapping[str, Any],
    chapter_number: int,
) -> None:
    """Keep Reviewer operations behind the durable Translator acceptance gate."""

    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise ReviewCliError("progress state must contain a chapters array")
    chapter = next(
        (
            item
            for item in chapters
            if isinstance(item, Mapping) and item.get("number") == chapter_number
        ),
        None,
    )
    if chapter is None:
        raise ReviewCliError(f"progress does not contain chapter {chapter_number}")
    evidence = chapter.get("translation_acceptance")
    if not isinstance(evidence, Mapping):
        raise ReviewCliError(
            f"chapter {chapter_number} requires current translation_acceptance evidence before review"
        )
    expected_unit = unit_id_for_chapter(chapter)
    if evidence.get("unit_id") != expected_unit:
        raise ReviewCliError(
            f"chapter {chapter_number} translation_acceptance unit identity is invalid"
        )
    if evidence.get("role") != "translator":
        raise ReviewCliError(
            f"chapter {chapter_number} translation_acceptance role must be translator"
        )
    workflow = metadata.get("workflow")
    workflow_revision = None
    if isinstance(workflow, Mapping):
        for key in ("resolved_revision", "requested_ref"):
            value = workflow.get(key)
            if isinstance(value, str) and value.strip():
                workflow_revision = value
                break
    if workflow_revision is None:
        raise ReviewCliError("metadata workflow revision is unavailable")
    if evidence.get("workflow_revision") != workflow_revision:
        raise ReviewCliError(
            f"chapter {chapter_number} translation_acceptance uses another workflow revision"
        )


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _resolution_json(resolution: ReviewResolution) -> dict[str, Any]:
    return {
        "chapter_number": resolution.chapter_number,
        "current_record": resolution.current_record,
        "history": list(resolution.history),
        "source_sha256": resolution.source_sha256,
        "state": resolution.state,
        "translation_sha256": resolution.translation_sha256,
        "unit_id": resolution.unit_id,
    }


def _record_json(result: ReviewRecordResult) -> dict[str, Any]:
    return {
        "ledger_revision": result.ledger_revision,
        "record": result.record,
    }


def _accept_json(result: AcceptReviewResult) -> dict[str, Any]:
    return {
        "changed": result.changed,
        "progress_revision": result.progress_revision,
        "status": result.status,
        "unit_id": result.unit_id,
    }


def _accept_translation_json(result: AcceptTranslationResult) -> dict[str, Any]:
    return {
        "changed": result.changed,
        "progress_revision": result.progress_revision,
        "status": result.status,
        "translation_sha256": result.translation_sha256,
        "unit_id": result.unit_id,
    }


def _write_review_report(book_dir: Path, markdown: str) -> str:
    storage = FilesystemStorage(book_dir)
    content = markdown.encode("utf-8")
    try:
        current = storage.read(REVIEW_REPORT_PATH)
    except StorageNotFound:
        try:
            storage.create_if_absent(REVIEW_REPORT_PATH, content)
        except StorageAlreadyExists as exc:
            raise ReviewCliError("review report changed concurrently; rerun generation") from exc
        except StorageError as exc:
            raise ReviewCliError(f"cannot create {REVIEW_REPORT_PATH}: {exc}") from exc
        return "generated"

    if current.content == content:
        return "unchanged"

    try:
        storage.write_if_version(REVIEW_REPORT_PATH, content, current.version)
    except StorageVersionConflict as exc:
        raise ReviewCliError("review report changed concurrently; rerun generation") from exc
    except StorageError as exc:
        raise ReviewCliError(f"cannot update {REVIEW_REPORT_PATH}: {exc}") from exc
    return "updated"


def review_diff_command(args: argparse.Namespace, root: Path) -> int:
    """Read-only proposed correction scope; never creates review evidence."""
    from .review_diff import plan

    book_dir, repository = _repository(root, args.slug)
    progress, _ = _load_progress(repository)
    metadata = _load_metadata(repository)
    _require_current_translation_acceptance(progress, metadata, args.chapter)
    chapter = next(ch for ch in progress["chapters"] if ch["number"] == args.chapter)
    unit = unit_id_for_chapter(chapter)
    ledger = repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER).data
    previous = next((row for row in ledger["records"]
                     if row["record_id"] == args.previous_record and row["unit_id"] == unit),
                    None)
    if previous is None or previous["outcome"] != "CORRECTIONS_REQUIRED":
        raise ReviewCliError("previous review must be a CORRECTIONS_REQUIRED record for this unit")
    if previous["source_sha256"] != chapter["translation_acceptance"]["source_sha256"]:
        raise ReviewCliError("source revision changed; full independent review required")
    try:
        old = Path(args.previous_translation).read_bytes()
        current = _artifact_reader(book_dir)(chapter["translation_path"])
        result = plan(old, current,
                      previous_sha256=previous["translation_sha256"],
                      current_sha256=chapter["translation_acceptance"]["translation_sha256"])
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReviewCliError(str(exc)) from exc
    result["unit_id"] = unit
    result["previous_record_id"] = args.previous_record
    _print_json(result)
    return 0


def review_preflight_command(args: argparse.Namespace, root: Path) -> int:
    """Read-only advisory checks after durable translation acceptance."""
    from .review_preflight import inspect

    book_dir, repository = _repository(root, args.slug)
    progress, _ = _load_progress(repository)
    metadata = _load_metadata(repository)
    _require_current_translation_acceptance(progress, metadata, args.chapter)
    chapter = next(ch for ch in progress["chapters"] if ch["number"] == args.chapter)
    read = _artifact_reader(book_dir)
    try:
        glossary = read("glossary.md").decode("utf-8")
        result = inspect(read(chapter["source_path"]), read(chapter["translation_path"]), glossary)
    except (OSError, UnicodeError) as exc:
        raise ReviewCliError(f"review preflight inputs are unavailable: {exc}") from exc
    evidence = chapter["translation_acceptance"]
    if (result["source_sha256"] != evidence["source_sha256"]
            or result["translation_sha256"] != evidence["translation_sha256"]):
        raise ReviewCliError("review preflight must run on the exact accepted source and translation")
    result["unit_id"] = unit_id_for_chapter(chapter)
    _print_json(result)
    return 0


def review_record_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, progress_revision = _load_progress(repository)
    metadata = _load_metadata(repository)
    _require_current_translation_acceptance(progress, metadata, args.chapter)
    manager = _manager(book_dir, repository)
    review_commit = args.review_commit if args.review_commit is not None else _git_head(root)
    quality_report = None
    if args.quality_report:
        try:
            path = Path(args.quality_report)
            if path.stat().st_size > 32768:
                raise ValueError("quality report exceeds 32 KiB")
            quality_report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ReviewCliError(f"cannot load quality report: {exc}") from exc
    try:
        result = manager.record(
            progress,
            progress_revision,
            metadata,
            args.chapter,
            outcome=args.outcome,
            reviewer_session_id=args.session_id,
            review_commit=review_commit,
            quality_report=quality_report,
        )
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    if args.json:
        _print_json(_record_json(result))
    else:
        record = result.record
        print(
            f"recorded {record['outcome']} for {record['unit_id']} "
            f"sequence={record['sequence']} translation_sha256={record['translation_sha256']}"
        )
    return 0


def reviews_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, _ = _load_progress(repository)
    metadata = _load_metadata(repository)
    manager = _manager(book_dir, repository)
    try:
        resolutions = manager.resolve_all(progress, metadata)
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    payload = {"reviews": [_resolution_json(item) for item in resolutions]}
    if args.json:
        _print_json(payload)
    else:
        for item in resolutions:
            print(
                f"{item.unit_id} state={item.state} "
                f"source_sha256={item.source_sha256 or '-'} "
                f"translation_sha256={item.translation_sha256 or '-'}"
            )
    return 0


def review_report_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, _ = _load_progress(repository)
    metadata = _load_metadata(repository)
    manager = _manager(book_dir, repository)
    try:
        snapshot = build_review_report_snapshot(manager, progress, metadata)
        if not args.json:
            markdown = render_review_report_markdown(snapshot)
    except (ReviewError, SchemaError, RepositoryError, StorageError, ValueError) as exc:
        raise ReviewCliError(str(exc)) from exc

    if args.json:
        _print_json(snapshot)
        return 0

    disposition = _write_review_report(book_dir, markdown)
    coverage = snapshot["summary"]["pass_coverage"]
    print(
        f"{disposition} books/{args.slug}/{REVIEW_REPORT_PATH} "
        f"pass={coverage['passed']}/{coverage['total']}"
    )
    return 0


def accept_translation_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, progress_revision = _load_progress(repository)
    metadata = _load_metadata(repository)
    manager = _translation_manager(book_dir, repository)
    try:
        result = manager.accept(
            progress,
            progress_revision,
            metadata,
            args.chapter,
            session_id=args.session_id,
        )
    except (TranslationAcceptanceError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    if args.json:
        _print_json(_accept_translation_json(result))
    else:
        suffix = "unchanged" if not result.changed else "updated"
        print(
            f"accepted {result.unit_id} as translated ({suffix}) "
            f"translation_sha256={result.translation_sha256}"
        )
    return 0


def accept_review_command(args: argparse.Namespace, root: Path) -> int:
    book_dir, repository = _repository(root, args.slug)
    progress, progress_revision = _load_progress(repository)
    metadata = _load_metadata(repository)
    _require_current_translation_acceptance(progress, metadata, args.chapter)
    manager = _manager(book_dir, repository)
    try:
        result = manager.accept_review(progress, progress_revision, metadata, args.chapter)
    except (ReviewError, SchemaError, RepositoryError, StorageError) as exc:
        raise ReviewCliError(str(exc)) from exc

    if args.json:
        _print_json(_accept_json(result))
    else:
        suffix = "unchanged" if not result.changed else "updated"
        print(f"accepted {result.unit_id} as reviewed ({suffix})")
    return 0


def _positive_chapter(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("chapter must be a positive integer") from exc
    if number < 1 or str(number) != value:
        raise argparse.ArgumentTypeError("chapter must be a positive integer")
    return number


def register_review_commands(subparsers: argparse._SubParsersAction, root: Path) -> None:
    """Register review evidence and workflow orchestration commands."""

    diff = subparsers.add_parser(
        "review-diff", help="Read-only, SHA-checked scoped correction review candidate."
    )
    diff.add_argument("slug")
    diff.add_argument("chapter", type=_positive_chapter)
    diff.add_argument("--previous-record", required=True)
    diff.add_argument("--previous-translation", required=True, metavar="FILE")
    diff.add_argument("--json", action="store_true")
    diff.set_defaults(func=lambda args: review_diff_command(args, root))

    preflight = subparsers.add_parser(
        "review-preflight", help="Read-only, hash-bound advisory QA before independent review."
    )
    preflight.add_argument("slug")
    preflight.add_argument("chapter", type=_positive_chapter)
    preflight.add_argument("--json", action="store_true", help="Emit advisory JSON.")
    preflight.set_defaults(func=lambda args: review_preflight_command(args, root))

    record = subparsers.add_parser("review-record", help="Record hash-bound Reviewer evidence.")
    record.add_argument("slug", help="Book slug under books/.")
    record.add_argument("chapter", type=_positive_chapter, help="Positive chapter number.")
    record.add_argument("--outcome", choices=("PASS", "CORRECTIONS_REQUIRED"), required=True)
    record.add_argument("--session-id", required=True, help="Owning reviewer session identity.")
    record.add_argument("--review-commit", help="Relevant Git commit; best-effort HEAD when omitted.")
    record.add_argument(
        "--quality-report", metavar="FILE",
        help="JSON evidence of independent/bounded review checks and findings.",
    )
    record.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    record.set_defaults(func=lambda args: review_record_command(args, root))

    reviews = subparsers.add_parser("reviews", help="Resolve current review evidence for every chapter.")
    reviews.add_argument("slug", help="Book slug under books/.")
    reviews.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    reviews.set_defaults(func=lambda args: reviews_command(args, root))

    report = subparsers.add_parser(
        "review-report",
        help="Generate deterministic review coverage evidence from the review ledger.",
    )
    report.add_argument("slug", help="Book slug under books/.")
    report.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic machine-readable JSON without writing REVIEW_REPORT.md.",
    )
    report.set_defaults(func=lambda args: review_report_command(args, root))

    accept_translation = subparsers.add_parser(
        "accept-translation",
        help="Promote one Translator artifact through its durable claim and shared-state snapshot.",
    )
    accept_translation.add_argument("slug", help="Book slug under books/.")
    accept_translation.add_argument(
        "chapter",
        type=_positive_chapter,
        help="Positive chapter number.",
    )
    accept_translation.add_argument(
        "--session-id",
        required=True,
        help="Owning translator session identity.",
    )
    accept_translation.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic machine-readable JSON.",
    )
    accept_translation.set_defaults(
        func=lambda args: accept_translation_command(args, root)
    )

    accept = subparsers.add_parser("accept-review", help="Promote one chapter using current PASS evidence.")
    accept.add_argument("slug", help="Book slug under books/.")
    accept.add_argument("chapter", type=_positive_chapter, help="Positive chapter number.")
    accept.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    accept.set_defaults(func=lambda args: accept_review_command(args, root))

    register_status_commands(subparsers, root, error_factory=ReviewCliError)

    from .epub_cli import register_epub_commands

    register_epub_commands(subparsers, root, error_factory=ReviewCliError)
