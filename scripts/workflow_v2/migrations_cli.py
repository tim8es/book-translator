"""CLI adapter for explicit Workflow v2 workspace upgrades."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .coordination import BookCoordinationManager
from .filesystem import FilesystemStorage
from .migrations import (
    CANONICAL_REPOSITORY,
    MigrationError,
    MigrationExecutor,
    MigrationPlanner,
    MigrationResult,
)
from .repository import WorkflowStateRepository
from .storage import StorageError


INSTALL_PROVENANCE_PATH = ".book-translator-install.json"


class MigrationCliError(RuntimeError):
    """Installed provenance or CLI wiring is unusable for an explicit upgrade."""


def load_install_provenance(root: Path) -> dict[str, str | None]:
    """Load the workflow revision actually installed at this runtime root."""

    path = Path(root) / INSTALL_PROVENANCE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MigrationCliError(f"cannot read {INSTALL_PROVENANCE_PATH}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MigrationCliError(f"invalid {INSTALL_PROVENANCE_PATH}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise MigrationCliError(f"{INSTALL_PROVENANCE_PATH} must be a JSON object")
    if raw.get("schema_version") != 1:
        raise MigrationCliError(f"{INSTALL_PROVENANCE_PATH} schema_version must equal 1")
    if raw.get("canonical_repository") != CANONICAL_REPOSITORY:
        raise MigrationCliError(
            f"{INSTALL_PROVENANCE_PATH} canonical_repository must equal {CANONICAL_REPOSITORY!r}"
        )

    requested_ref = raw.get("requested_ref")
    if requested_ref is not None and (
        not isinstance(requested_ref, str) or not requested_ref.strip()
    ):
        raise MigrationCliError(
            f"{INSTALL_PROVENANCE_PATH} requested_ref must be null or a non-empty string"
        )
    resolved_revision = raw.get("resolved_revision")
    if not isinstance(resolved_revision, str) or not resolved_revision.strip():
        raise MigrationCliError(
            f"{INSTALL_PROVENANCE_PATH} resolved_revision must be a non-empty string"
        )
    install_root = raw.get("install_root")
    if not isinstance(install_root, str) or not install_root.strip():
        raise MigrationCliError(
            f"{INSTALL_PROVENANCE_PATH} install_root must be a non-empty string"
        )

    return {
        "canonical_repository": CANONICAL_REPOSITORY,
        "requested_ref": requested_ref,
        "resolved_revision": resolved_revision,
    }


def _book_directory(root: Path, slug: str) -> Path:
    if (
        not isinstance(slug, str)
        or not slug
        or "/" in slug
        or "\\" in slug
        or slug in {".", ".."}
    ):
        raise MigrationCliError("book slug must be one directory name under books/")
    books_root = (Path(root) / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise MigrationCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise MigrationCliError(f"Book directory does not exist: books/{slug}")
    return book_dir


def _artifact_reader(book_dir: Path) -> Callable[[str], bytes]:
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


def _payload(result: MigrationResult) -> dict[str, Any]:
    return {
        "book_slug": result.book_slug,
        "from_revision": result.from_revision,
        "lifecycle_downgrades": list(result.lifecycle_downgrades),
        "migrated_paths": list(result.migrated_paths),
        "outcome": result.outcome,
        "to_revision": result.to_revision,
    }


def workflow_upgrade_command(
    args: argparse.Namespace,
    root: Path,
    *,
    error_factory: Callable[[str], Exception],
) -> int:
    try:
        installed = load_install_provenance(root)
        book_dir = _book_directory(root, args.slug)
        repository = WorkflowStateRepository(FilesystemStorage(book_dir))
        planner = MigrationPlanner(
            repository,
            book_dir=book_dir,
            artifact_reader=_artifact_reader(book_dir),
        )
        coordination = BookCoordinationManager(repository)
        executor = MigrationExecutor(repository, planner, coordination=coordination)
        session_id = f"workflow-upgrade:{args.slug}"

        result = executor.recover(session_id=session_id, installed=installed)
        if result is None:
            plan = planner.plan(
                slug=args.slug,
                to_revision=args.to_revision,
                installed=installed,
            )
            result = executor.execute(plan, session_id=session_id)
    except (MigrationCliError, MigrationError, StorageError, OSError, ValueError) as exc:
        raise error_factory(str(exc)) from exc

    payload = _payload(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        paths = ",".join(result.migrated_paths) if result.migrated_paths else "-"
        downgrades = ",".join(str(item) for item in result.lifecycle_downgrades) or "-"
        print(
            f"workflow-upgrade {result.book_slug}: outcome={result.outcome} "
            f"from={result.from_revision or '-'} to={result.to_revision} "
            f"paths={paths} reviewed_to_translated={downgrades}"
        )
    return 0


def register_migration_command(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    error_factory: Callable[[str], Exception],
) -> None:
    """Register the explicit, recoverable workflow-upgrade command."""

    upgrade = subparsers.add_parser(
        "workflow-upgrade",
        help="Explicitly upgrade one book workspace to the installed Workflow v2 revision.",
    )
    upgrade.add_argument("slug", help="Book slug under books/.")
    upgrade.add_argument(
        "--to",
        dest="to_revision",
        required=True,
        help="Installed resolved workflow revision to adopt.",
    )
    upgrade.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic machine-readable JSON.",
    )
    upgrade.set_defaults(
        func=lambda args: workflow_upgrade_command(
            args,
            root,
            error_factory=error_factory,
        )
    )
