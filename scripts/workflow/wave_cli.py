"""CLI surface for wave planning and bounded GitHub-compatible state transitions."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from .filesystem import FilesystemStorage
from .literary_memory import MemoryError, context_pack
from .session_planner import SessionPlanError, plan_sessions
from .session_runtime import SessionRuntimeError, save_dispatch, next_action, load_dispatch
from .waves import WaveError, WaveManager


def _decode(path: str, *, max_bytes: int = 65536):
    p = Path(path).expanduser()
    if not p.is_file() or p.stat().st_size > max_bytes:
        raise WaveError("JSON argument is missing or larger than the allowed limit")
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise WaveError(f"invalid input JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WaveError("JSON argument must be an object")
    return value


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def register_wave_commands(subparsers, root: Path, preflight, error_factory) -> None:
    """Registration avoids separate workflow authority or separate repository."""
    from .status_cli import _book_directory, _snapshot, _artifact_reader

    def command(args) -> int:
        try:
            book_dir = _book_directory(root, args.slug)
            storage = FilesystemStorage(book_dir)
            manager = WaveManager(storage)
            snapshot = _snapshot(args, root, preflight)
            from .schemas import SchemaKind
            from .repository import WorkflowStateRepository
            repository = WorkflowStateRepository(storage)
            metadata = repository.read("metadata.json", SchemaKind.METADATA).data
            progress = repository.read("progress.json", SchemaKind.PROGRESS).data
            reader = _artifact_reader(book_dir)
            units = {u["unit_id"]: u for u in snapshot.get("units", [])}
            action = args.command
            if action == "wave-plan":
                result = manager.plan(snapshot, count=args.count)
            elif action == "wave-start":
                result = manager.start(
                    snapshot, metadata, count=args.count, wave_id=args.wave_id
                )
            elif action == "wave-status":
                result = manager.status(snapshot)
            elif action == "wave-dispatch":
                manifest, state, _ = manager._active_state()
                if state["phase"] != "ACTIVE":
                    raise WaveError("dispatch requires ACTIVE wave")
                if not snapshot["valid"]:
                    raise WaveError("preflight failed")
                filtered = dict(snapshot)
                filtered["units"] = [u for u in snapshot["units"]
                                     if u["unit_id"] in manifest["unit_ids"]]
                filtered["claims"] = [c for c in snapshot["claims"]
                                      if c["unit_id"] in manifest["unit_ids"]]
                sizes = {u["unit_id"]: len(reader(u["source_path"]))
                         for u in filtered["units"]}
                base = args.base_commit
                if not base:
                    from .status_cli import _git_head
                    base = _git_head(root)
                if not base:
                    raise WaveError("Git HEAD required; use --base-commit")
                dispatch = plan_sessions(
                    filtered, sessions=args.sessions, source_sizes=sizes,
                    base_commit=base, dispatch_id=args.dispatch_id or uuid4().hex,
                )
                for session in dispatch["sessions"]:
                    session["context"]["wave_id"] = manifest["wave_id"]
                    session["context"]["memory_revision"] = manifest["memory_revision"]
                save_dispatch(storage, dispatch)
                result = dispatch
            elif action == "wave-next":
                manifest, state, _ = manager._active_state()
                if state["phase"] != "ACTIVE":
                    raise WaveError("wave is not active for worker admission")
                plan = load_dispatch(storage, args.dispatch_id)
                session = next((s for s in plan["sessions"]
                                if s["session_id"] == args.session_id), None)
                if session is None or session["context"].get("wave_id") != manifest["wave_id"]:
                    raise WaveError("session dispatch belongs to another wave")
                if session["context"].get("memory_revision") != manifest["memory_revision"]:
                    raise WaveError("session uses stale literary memory")
                if any(u["unit_id"] not in manifest["unit_ids"] for u in session["units"]):
                    raise WaveError("session has assignments outside the active wave")
                result = next_action(plan, snapshot, args.session_id)
            elif action == "memory-context":
                manifest, state, _ = manager._active_state()
                if args.unit_id not in manifest["unit_ids"]:
                    raise WaveError("memory context may only address an active-wave unit")
                entry = units.get(args.unit_id)
                if entry is None:
                    raise WaveError("requested unit is unavailable")
                previous = [u["unit_id"] for u in snapshot["units"]
                            if u["chapter_number"] < entry["chapter_number"]]
                result = context_pack(manager.memory(), unit_id=args.unit_id,
                                      source=reader(entry["source_path"]),
                                      budget_chars=args.budget_chars,
                                      previous_units=previous)
            elif action == "memory-propose":
                manifest, state, _ = manager._active_state()
                proposal = _decode(args.entry)
                evidence = proposal.get("evidence") or {}
                if not isinstance(evidence, Mapping) or evidence.get("unit_id") not in units:
                    raise WaveError("proposal must reference an existing chapter")
                unit_id = evidence["unit_id"]
                path = manager.submit(
                    manifest["wave_id"], args.session_id, proposal,
                    source=reader(units[unit_id]["source_path"]),
                    source_revision=manifest["source_revision_id"],
                )
                result = {"state": "created_or_identical", "path": path}
            elif action == "wave-reconcile":
                decisions = _decode(args.decisions)
                manifest, state, _ = manager._active_state()
                if args.prepare:
                    result = manager.prepare_close(
                        snapshot, decisions=decisions,
                        read_source=reader, read_translation=reader,
                    )
                else:
                    result = manager.reconcile_preview(
                        snapshot, decisions=decisions,
                        read_source=reader, read_translation=reader,
                    )
                # No prose is written in preview mode.
                result = {
                    "wave_id": result["wave_id"],
                    "decision_count": len(result["decisions"]),
                    "next_memory_revision": result["next_snapshot"]["revision"],
                    "impact_candidates": result["impact_candidates"],
                    "prepared": bool(args.prepare),
                }
            elif action == "wave-close":
                manifest, state, _ = manager._active_state()
                if state["phase"] != "RECONCILING" or not snapshot["valid"] or snapshot["claims"]:
                    raise WaveError("cannot close without valid preflight, no claims and RECONCILING state")
                by_id = {u["unit_id"]: u for u in snapshot["units"]}
                if any(by_id.get(uid, {}).get("lifecycle") != "reviewed"
                       or by_id[uid].get("review") != "pass"
                       for uid in manifest["unit_ids"]):
                    raise WaveError("one or more assigned units lacks current PASS")
                result = manager.close(expected_wave=manifest["wave_id"])
            elif action == "wave-recover":
                # Requires state reconciliation, but never fabricates missing decisions.
                manifest, state, _ = manager._active_state()
                if not snapshot["valid"] or snapshot["claims"]:
                    raise WaveError("cannot recover wave during invalid preflight or active claims")
                result = manager.recover(wave_id=manifest["wave_id"])
            elif action == "memory-impact":
                result = {"pending": manager.impact_queue()}
            elif action == "memory-impact-resolve":
                ledger = repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER).data
                selected = [r for r in ledger["records"] if r["record_id"] == args.record_id]
                if len(selected) != 1:
                    raise WaveError("exact review record not found")
                unit = units.get(args.unit_id)
                if unit is None:
                    raise WaveError("unit not in current book")
                import hashlib
                sha = hashlib.sha256(reader(unit["translation_path"])).hexdigest()
                claim, _ = manager._active_state() if False else (None, None)
                from .waves import _json_read
                claim, _ = _json_read(storage, f".workflow/claims/{args.unit_id}.json")
                path = manager.resolve_impact(
                    wave_id=args.wave_id, memory_id=args.memory_id,
                    unit_id=args.unit_id, review_record=selected[0],
                    current_translation_sha256=sha, reviewer_claim=claim,
                )
                result = {"state": "verified", "path": path}
            else:
                raise WaveError("unknown wave command")
        except (WaveError, MemoryError, SessionRuntimeError, SessionPlanError,
                ValueError, OSError) as exc:
            raise error_factory(str(exc)) from exc
        _emit(result)
        return 0

    defs = {
        "wave-plan": "Preview a bounded next wave",
        "wave-start": "Create an immutable wave and active pointer",
        "wave-status": "Read current wave and reviewed-unit readiness",
        "wave-dispatch": "Save wave-pinned per-chat workpacks",
        "wave-next": "Resume one worker within a pinned wave",
        "memory-context": "Generate bounded source-grounded literary context",
        "memory-propose": "Submit a claim-authorized source-grounded memory proposal",
        "wave-reconcile": "Preview or prepare frozen wave reconciliation",
        "wave-close": "Publish verified memory and close a reconciled wave",
        "wave-recover": "Finish a frozen interrupted wave reconciliation",
        "memory-impact": "List pending consistency checks",
        "memory-impact-resolve": "Bind impact resolution to an independent PASS",
    }
    for name, help_text in defs.items():
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument("slug")
        if name in {"wave-plan", "wave-start"}:
            p.add_argument("--count", type=int, default=6)
        if name == "wave-start":
            p.add_argument("--wave-id")
        if name == "wave-dispatch":
            p.add_argument("--sessions", type=int, required=True)
            p.add_argument("--base-commit")
            p.add_argument("--dispatch-id")
        if name == "wave-next":
            p.add_argument("--dispatch-id", required=True)
            p.add_argument("--session-id", required=True)
        if name == "memory-context":
            p.add_argument("--unit-id", required=True)
            p.add_argument("--budget-chars", type=int, default=6000)
        if name == "memory-propose":
            p.add_argument("--entry", required=True)
            p.add_argument("--session-id", required=True)
        if name == "wave-reconcile":
            p.add_argument("--decisions", required=True)
            p.add_argument("--prepare", action="store_true")
        if name == "memory-impact-resolve":
            for flag in ("wave-id", "memory-id", "unit-id", "record-id"):
                p.add_argument("--" + flag, required=True)
        p.set_defaults(func=command)
