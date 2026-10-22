"""Argparse integration for read-only current-workflow status and resume."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
from uuid import uuid4
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .filesystem import FilesystemStorage
from .repository import RepositoryError, WorkflowStateRepository
from .session_planner import SessionPlanError, plan_sessions
from .session_runtime import SessionRuntimeError, save_dispatch, load_dispatch, next_action
from .session_benchmark import BenchmarkError, evaluate_file
from .schemas import SchemaError
from .source_cli import normalize_structural_errors, register_source_overrides, source_storage_mode
from .source_coverage import coverage_integrity_errors
from .status import StatusError, StatusResolver
from .storage import StorageError


class StatusCliError(RuntimeError):
    """Expected status/resume command error suitable for Book Translator CLI output."""


Preflight = Callable[[str], tuple[Sequence[str], Mapping[str, Any]]]
ErrorFactory = Callable[[str], Exception]


def _book_directory(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise StatusCliError("book slug must be one directory name under books/")
    books_root = (root / "books").resolve(strict=False)
    book_dir = (books_root / slug).resolve(strict=False)
    try:
        book_dir.relative_to(books_root)
    except ValueError as exc:
        raise StatusCliError("book slug escapes books/") from exc
    if not book_dir.is_dir():
        raise StatusCliError(f"Book directory does not exist: books/{slug}")
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


def _resolver(root: Path, slug: str) -> StatusResolver:
    book_dir = _book_directory(root, slug)
    repository = WorkflowStateRepository(FilesystemStorage(book_dir))
    return StatusResolver(repository, artifact_reader=_artifact_reader(book_dir))


def default_preflight(root: Path, slug: str) -> tuple[Sequence[str], Mapping[str, Any]]:
    """Run structural and sealed-corpus validation for a supported workspace."""

    try:
        book_module = importlib.import_module("book")
        # The status resolver verifies every review; do not repeat that expensive pass.
        structural_errors, _ = book_module.validate_book(slug, check_review_evidence=False)
        book_dir, metadata, progress = book_module.load_book(slug)
        structural_errors = normalize_structural_errors(book_dir, metadata, structural_errors)
    except Exception as exc:
        raise StatusCliError(f"cannot run structural preflight: {exc}") from exc

    mode = source_storage_mode(metadata)
    if mode is None:
        structural_errors.extend(coverage_integrity_errors(book_dir, metadata, progress))
        return structural_errors, {
            "state": "invalid",
            "error": "metadata.json source identity is required by the current workflow",
        }

    manifest_path = book_dir / "source-manifest.json"
    if not manifest_path.is_file():
        structural_errors.extend(coverage_integrity_errors(book_dir, metadata, progress))
        return structural_errors, {
            "state": "invalid",
            "storage_mode": mode,
            "error": "source-manifest.json is required by the current workflow",
        }

    try:
        corpus_module = importlib.import_module("corpus")
        manifest = corpus_module.load_source_manifest(book_dir)
        if manifest is None:
            raise StatusCliError("source-manifest.json is required by the current workflow")
        verified = corpus_module.verify_manifest(book_dir, metadata, progress, manifest)
    except Exception as exc:
        # An invalid seal cannot justify trusting its declared chapter hashes.
        structural_errors.extend(coverage_integrity_errors(book_dir, metadata, progress))
        payload: dict[str, Any] = {
            "state": "invalid",
            "storage_mode": mode,
            "error": str(exc),
        }
        return structural_errors, payload

    # The manifest verifier has just hashed all extracted chapter bytes.
    fingerprints = {item["path"]: item["sha256"] for item in manifest["extracted"]}
    structural_errors.extend(coverage_integrity_errors(
        book_dir, metadata, progress, verified_extracted_sha256=fingerprints
    ))
    return structural_errors, dict(verified)


def _snapshot(
    args: argparse.Namespace,
    root: Path,
    preflight: Preflight,
) -> dict[str, Any]:
    resolver = _resolver(root, args.slug)
    structural_errors, corpus = preflight(args.slug)
    try:
        return resolver.status(structural_errors=structural_errors, corpus=corpus)
    except (StatusError, SchemaError, RepositoryError, StorageError) as exc:
        raise StatusCliError(str(exc)) from exc


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


def _parallel_dispatch_snapshot(
    status: Mapping[str, Any],
    root: Path,
    *,
    explicit_commit: str | None = None,
) -> tuple[str, dict[str, str]]:
    base_commit = explicit_commit or _git_head(root)
    if base_commit is None:
        raise StatusCliError(
            "parallel resume requires Git HEAD so dispatch can bind a fixed base_commit"
        )

    revisions = status.get("state_revisions")
    if not isinstance(revisions, Mapping):
        raise StatusCliError("parallel resume shared-state revisions are unavailable")

    shared: dict[str, str] = {}
    for key in ("glossary", "style_guide"):
        revision = revisions.get(key)
        if not isinstance(revision, str) or not revision.strip():
            raise StatusCliError(f"parallel resume requires current {key} revision")
        shared[key] = revision
    return base_commit, shared


def _print_json(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _counts(label: str, values: Mapping[str, Any]) -> str:
    return label + " " + " ".join(f"{key}={values[key]}" for key in sorted(values))


def status_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    payload = _snapshot(args, root, preflight)
    if args.json:
        _print_json(payload)
        return 0

    state = "valid" if payload["valid"] else "invalid"
    print(f"{args.slug}: {state} workflow={payload.get('workflow_revision') or '-'}")
    print(_counts("lifecycle", payload["lifecycle"]))
    print(_counts("reviews", payload["reviews"]))
    corpus = payload["corpus"]
    line = f"claims={len(payload['claims'])} corpus={corpus.get('state', 'unknown')}"
    mode = corpus.get("storage_mode")
    if mode:
        attached = corpus.get("source_attached")
        attached_text = "yes" if attached is True else "no" if attached is False else "unknown"
        line += f" source={mode} attached={attached_text}"
    print(line)
    for error in payload["errors"]:
        print(f"ERROR: {error}")
    return 0


def plan_sessions_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    """Create advisory, balanced workpacks without taking claims or writing state."""
    status = _snapshot(args, root, preflight)
    base_commit, _ = _parallel_dispatch_snapshot(
        status, root, explicit_commit=args.base_commit
    )
    reader = _artifact_reader(_book_directory(root, args.slug))
    sizes: dict[str, int] = {}
    try:
        for unit in status["units"]:
            if unit["lifecycle"] != "reviewed":
                sizes[unit["unit_id"]] = len(reader(unit["source_path"]))
        plan = plan_sessions(
            status, sessions=args.sessions, source_sizes=sizes,
            base_commit=base_commit, dispatch_id=args.dispatch_id or uuid4().hex,
        )
        if args.save:
            save_dispatch(FilesystemStorage(_book_directory(root, args.slug)), plan)
    except (OSError, SessionPlanError, SessionRuntimeError) as exc:
        raise StatusCliError(str(exc)) from exc
    if args.json:
        _print_json(plan)
    else:
        print(f"book={args.slug} remaining={plan['remaining_units']} sessions={len(plan['sessions'])}")
        for assignment in plan["sessions"]:
            chapters = ",".join(str(number) for number in assignment["chapter_numbers"])
            print(f"session={assignment['session_id']} chapters={chapters} source_bytes={assignment['source_bytes']}")
    return 0


def benchmark_sessions_command(args: argparse.Namespace) -> int:
    """Analyze supplied observations; no timers, LLM calls or workspace writes."""
    try:
        payload = evaluate_file(Path(args.record).expanduser())
    except (OSError, BenchmarkError) as exc:
        raise StatusCliError(str(exc)) from exc
    if args.json:
        _print_json(payload)
    else:
        print(f"dataset={payload['dataset_id']} protocol={payload['evaluation_protocol']}")
        for row in payload["summary"]:
            print(
                f"sessions={row['sessions']} samples={row['samples']} "
                f"words_per_hour={row['median_words_per_hour']}"
            )
        for comparison in payload["comparisons"]:
            print(
                f"sessions={comparison['sessions']} speedup={comparison['throughput_speedup']} "
                f"provisional_target={comparison['meets_provisional_2x_target']}"
            )
        print(payload["qualification"])
    return 0


def review_next_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    """Freshly select an accepted translation for a long-lived Reviewer chat."""
    from .review_queue import next_review
    from .waves import WaveManager, WaveError

    status = _snapshot(args, root, preflight)
    storage = FilesystemStorage(_book_directory(root, args.slug))
    manager = WaveManager(storage)
    wave = None
    try:
        if manager.active() is not None:
            manifest, state, _ = manager._active_state()
            if state["phase"] != "ACTIVE":
                decision = {
                    "schema_version": 1, "action": "blocked",
                    "reason": "wave_reconciliation_active", "session_id": args.session_id,
                }
                _print_json(decision)
                return 1
            if manager.memory()["revision"] != manifest["memory_revision"]:
                raise WaveError("active wave literary memory revision changed")
            wave = manifest
        decision = next_review(status, args.session_id, wave=wave)
        if decision["action"] in {"claim", "work"}:
            base_commit, shared = _parallel_dispatch_snapshot(
                status, root, explicit_commit=args.base_commit,
            )
            decision["claim_snapshot"] = {
                "base_commit": base_commit, "shared_state_revisions": shared,
                "workflow_revision": status["workflow_revision"],
                "source_sha256": status["corpus"]["source_sha256"],
            }
            if wave is not None:
                from .wave_cli import _wave_load_plan, _read_role_routing
                routed = dict(decision, context={
                    "wave_id": wave["wave_id"],
                    "memory_revision": wave["memory_revision"],
                    "source_sha256": wave["source_sha256"],
                    "workflow_revision": wave["workflow_revision"],
                    "shared_state_revisions": wave["shared_state_revisions"],
                })
                decision["load_plan"] = _wave_load_plan(
                    routed, status, wave, _read_role_routing(root),
                )
    except (ValueError, WaveError) as exc:
        raise StatusCliError(str(exc)) from exc
    _print_json(decision)
    return 1 if decision["action"] == "blocked" else 0


def session_next_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    """Read a saved workpack and derive the next safe step from current state."""
    status = _snapshot(args, root, preflight)
    storage = FilesystemStorage(_book_directory(root, args.slug))
    try:
        plan = load_dispatch(storage, args.dispatch_id)
        decision = next_action(plan, status, args.session_id)
    except SessionRuntimeError as exc:
        raise StatusCliError(str(exc)) from exc
    if args.json:
        _print_json(decision)
    else:
        print(f"session={decision['session_id']} next={decision['action']}"
              + (f" unit={decision['unit_id']}" if "unit_id" in decision else "")
              + (f" reason={decision['reason']}" if "reason" in decision else ""))
    return 1 if decision["action"] == "blocked" else 0


def resume_command(args: argparse.Namespace, root: Path, preflight: Preflight) -> int:
    status = _snapshot(args, root, preflight)
    resolver = _resolver(root, args.slug)
    try:
        payload = resolver.resume(status, parallel=getattr(args, "parallel", None))
    except StatusError as exc:
        raise StatusCliError(str(exc)) from exc

    if payload["operation"] == "parallel":
        base_commit, shared_state_revisions = _parallel_dispatch_snapshot(
            status, root, explicit_commit=getattr(args, "base_commit", None)
        )
        assignments = payload.get("assignments")
        if not isinstance(assignments, list):
            raise StatusCliError("parallel resume returned invalid assignments")
        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                raise StatusCliError("parallel resume returned invalid assignment")
            context = assignment.get("context")
            if not isinstance(context, dict):
                raise StatusCliError("parallel resume assignment context is invalid")
            context["base_commit"] = base_commit
            context["shared_state_revisions"] = dict(shared_state_revisions)

    if args.json:
        _print_json(payload)
    elif payload["operation"] == "blocked":
        unit = payload.get("unit_id")
        suffix = f" unit={unit}" if unit else ""
        print(f"next=blocked reason={payload.get('reason')}{suffix}")
        for error in payload.get("errors", ()):
            print(f"ERROR: {error}")
    elif payload["operation"] == "complete":
        print("next=complete")
    elif payload["operation"] == "finalize":
        print(f"next=finalize role={payload['context']['role']}")
    elif payload["operation"] == "parallel":
        assignments = payload["assignments"]
        print(f"next=parallel requested={payload['parallel']} assignments={len(assignments)}")
        print(
            "claim-snapshot "
            f"--base-commit {base_commit} "
            f"--glossary-revision {shared_state_revisions['glossary']} "
            f"--style-guide-revision {shared_state_revisions['style_guide']}"
        )
        for assignment in assignments:
            print(
                f"unit={assignment['unit_id']} chapter={assignment['chapter_number']} "
                f"operation={assignment['operation']} role={assignment['context']['role']}"
            )
    else:
        print(
            f"next={payload['operation']} unit={payload['unit_id']} "
            f"chapter={payload['chapter_number']} role={payload['context']['role']}"
        )

    return 1 if payload["operation"] == "blocked" else 0


def _adapt_errors(command: Callable[[argparse.Namespace], int], error_factory: ErrorFactory):
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except StatusCliError as exc:
            raise error_factory(str(exc)) from exc

    return run


def register_status_commands(
    subparsers: argparse._SubParsersAction,
    root: Path,
    *,
    preflight: Preflight | None = None,
    error_factory: ErrorFactory = StatusCliError,
) -> None:
    """Register explicit-source overrides, status/resume, and finalize orchestration."""

    register_source_overrides(subparsers, root, error_factory=error_factory)
    from .source_update_cli import register_source_update_commands
    register_source_update_commands(subparsers, root, error_factory=error_factory)
    resolved_preflight = preflight or (lambda slug: default_preflight(root, slug))

    status = subparsers.add_parser("status", help="Report repository-authoritative workflow status.")
    status.add_argument("slug", help="Book slug under books/.")
    status.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    status.set_defaults(
        func=_adapt_errors(
            lambda args: status_command(args, root, resolved_preflight),
            error_factory,
        )
    )

    resume = subparsers.add_parser("resume", help="Select the next valid operation without mutating state.")
    resume.add_argument("slug", help="Book slug under books/.")
    resume.add_argument("--json", action="store_true", help="Emit deterministic machine-readable JSON.")
    resume.add_argument(
        "--base-commit",
        help="Explicit HEAD SHA from GitHub for environments without local Git.",
    )
    resume.add_argument(
        "--parallel",
        type=int,
        metavar="N",
        help="Plan up to N disjoint worker assignments for this invocation only (N > 1).",
    )
    resume.set_defaults(
        func=_adapt_errors(
            lambda args: resume_command(args, root, resolved_preflight),
            error_factory,
        )
    )

    sessions = subparsers.add_parser(
        "plan-sessions", help="Generate balanced, read-only workpacks for manually opened chats."
    )
    sessions.add_argument("slug", help="Book slug under books/.")
    sessions.add_argument("--sessions", type=int, required=True, metavar="N")
    sessions.add_argument("--base-commit", help="Explicit repository HEAD SHA when local Git is unavailable.")
    sessions.add_argument(
        "--dispatch-id", help="Reuse the 32-hex ID only when replaying the SAME saved plan."
    )
    sessions.add_argument("--save", action="store_true", help="Atomically persist workpacks for chat restarts.")
    sessions.add_argument("--json", action="store_true")
    sessions.set_defaults(
        func=_adapt_errors(
            lambda args: plan_sessions_command(args, root, resolved_preflight),
            error_factory,
        )
    )

    live_reviewer = subparsers.add_parser(
        "review-next", help="Select next currently accepted chapter for independent Reviewer."
    )
    live_reviewer.add_argument("slug")
    live_reviewer.add_argument("--session-id", required=True)
    live_reviewer.add_argument("--base-commit",
                               help="Pinned Git HEAD from GitHub when local Git is unavailable.")
    live_reviewer.add_argument("--json", action="store_true")
    live_reviewer.set_defaults(func=_adapt_errors(
        lambda args: review_next_command(args, root, resolved_preflight),
        error_factory,
    ))

    next_session = subparsers.add_parser(
        "session-next", help="Resume one saved chat workpack from authoritative GitHub/file state."
    )
    next_session.add_argument("slug", help="Book slug under books/.")
    next_session.add_argument("--dispatch-id", required=True)
    next_session.add_argument("--session-id", required=True)
    next_session.add_argument("--json", action="store_true")
    next_session.set_defaults(
        func=_adapt_errors(
            lambda args: session_next_command(args, root, resolved_preflight),
            error_factory,
        )
    )

    benchmark = subparsers.add_parser(
        "benchmark-sessions", help="Compare measured 1/N-chat runs on an identical book corpus."
    )
    benchmark.add_argument("record", help="JSON file containing measured, independently reviewed runs.")
    benchmark.add_argument("--json", action="store_true")
    benchmark.set_defaults(
        func=_adapt_errors(benchmark_sessions_command, error_factory)
    )

    from .wave_cli import register_wave_commands
    register_wave_commands(subparsers, root, resolved_preflight, error_factory)

    from .finalize_cli import register_finalize_command

    register_finalize_command(
        subparsers,
        root,
        preflight=resolved_preflight,
        error_factory=error_factory,
    )
