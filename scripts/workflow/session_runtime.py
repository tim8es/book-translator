"""Safe resume planning for user-opened independent chat sessions.

The dispatcher is immutable. Actual completion, claims and review outcomes are
derived on every call from repository-authoritative status; no shadow progress
log is used. This module never acquires a claim or writes book artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .storage import StorageAlreadyExists, StorageBackend, StorageError, StorageNotFound


DISPATCH_PREFIX = ".workflow/session-dispatches"
HEX_ID = re.compile(r"^[0-9a-f]{32}$")
UNIT_ID = re.compile(r"^chapter-[0-9]{6}$")
SESSION_ID = re.compile(r"^bt-[0-9a-f]{16}$")


class SessionRuntimeError(ValueError):
    """A saved dispatch or current unit state is unusable."""


def _encoded(plan: Mapping[str, Any]) -> bytes:
    return (json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _path(dispatch_id: str) -> str:
    if not isinstance(dispatch_id, str) or HEX_ID.fullmatch(dispatch_id) is None:
        raise SessionRuntimeError("dispatch_id must be 32 lowercase hexadecimal characters")
    return f"{DISPATCH_PREFIX}/{dispatch_id}.json"


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if not isinstance(plan, Mapping) or plan.get("schema_version") != 1:
        raise SessionRuntimeError("dispatch plan schema_version must be 1")
    dispatch_id = plan.get("dispatch_id")
    _path(dispatch_id)
    if not isinstance(plan.get("book_slug"), str) or not plan["book_slug"].strip():
        raise SessionRuntimeError("dispatch plan lacks book slug")
    if not isinstance(plan.get("base_commit"), str) or not plan["base_commit"].strip():
        raise SessionRuntimeError("dispatch plan lacks base commit")
    assignments = plan.get("sessions")
    if not isinstance(assignments, list) or len(assignments) > 100:
        raise SessionRuntimeError("dispatch plan sessions must be a bounded list")
    seen_sessions: set[str] = set()
    seen_units: set[str] = set()
    for number, session in enumerate(assignments, 1):
        if not isinstance(session, Mapping) or not isinstance(session.get("units"), list):
            raise SessionRuntimeError(f"invalid workpack {number}")
        session_id = session.get("session_id")
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            raise SessionRuntimeError(f"workpack {number} lacks a valid session_id")
        if session_id in seen_sessions:
            raise SessionRuntimeError("duplicate session in dispatch")
        seen_sessions.add(session_id)
        units: list[str] = []
        for unit in session["units"]:
            if not isinstance(unit, Mapping):
                raise SessionRuntimeError("workpack unit must be an object")
            unit_id = unit.get("unit_id")
            if not isinstance(unit_id, str) or not UNIT_ID.fullmatch(unit_id):
                raise SessionRuntimeError("invalid workpack unit identity")
            if unit_id in seen_units:
                raise SessionRuntimeError("dispatch assigns a unit to multiple chats")
            if unit.get("role") not in {"translator", "reviewer"}:
                raise SessionRuntimeError("invalid workpack unit role")
            if type(unit.get("chapter_number")) is not int or unit["chapter_number"] < 1:
                raise SessionRuntimeError("invalid workpack chapter number")
            seen_units.add(unit_id)
            units.append(unit_id)
        identity = json.dumps({
            "book": plan["book_slug"], "base_commit": plan["base_commit"],
            "dispatch_id": dispatch_id, "units": units, "session": number,
        }, sort_keys=True).encode("utf-8")
        expected = "bt-" + hashlib.sha256(identity).hexdigest()[:16]
        if session_id != expected:
            raise SessionRuntimeError("workpack session identity differs from its dispatch")
        context = session.get("context")
        if not isinstance(context, Mapping):
            raise SessionRuntimeError("workpack missing pinned context")
        revisions = context.get("shared_state_revisions")
        if (context.get("base_commit") != plan["base_commit"]
                or not isinstance(context.get("workflow_revision"), str)
                or not isinstance(revisions, Mapping)
                or any(not isinstance(revisions.get(key), str) for key in ("glossary", "style_guide"))):
            raise SessionRuntimeError("workpack pinned context is invalid")
    if plan.get("remaining_units") != len(seen_units):
        raise SessionRuntimeError("workpack coverage differs from dispatch unit count")


def save_dispatch(storage: StorageBackend, plan: Mapping[str, Any]) -> str:
    """Create-once persistent plan; identical replay is safe, divergent replay fails."""
    _validate_plan(plan)
    path = _path(plan["dispatch_id"])
    payload = _encoded(plan)
    try:
        storage.create_if_absent(path, payload)
    except StorageAlreadyExists:
        try:
            saved = storage.read(path)
        except StorageError as exc:
            raise SessionRuntimeError(f"cannot read existing dispatch: {exc}") from exc
        if saved.content != payload:
            raise SessionRuntimeError("dispatch ID already stores a different plan; create a new dispatch")
    except StorageError as exc:
        raise SessionRuntimeError(f"cannot persist dispatch: {exc}") from exc
    return path


def load_dispatch(storage: StorageBackend, dispatch_id: str) -> dict[str, Any]:
    path = _path(dispatch_id)
    try:
        payload = storage.read(path).content
    except StorageNotFound as exc:
        raise SessionRuntimeError("saved dispatch not found; run plan-sessions --save") from exc
    except StorageError as exc:
        raise SessionRuntimeError(f"cannot load dispatch: {exc}") from exc
    try:
        plan = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise SessionRuntimeError("saved dispatch JSON is invalid") from exc
    _validate_plan(plan)
    if plan["dispatch_id"] != dispatch_id:
        raise SessionRuntimeError("saved dispatch identifier does not match its path")
    return plan


def _now_utc(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise SessionRuntimeError("session clock must be timezone-aware")
    return current.astimezone(timezone.utc)


def _expiry(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SessionRuntimeError("claim expiry is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SessionRuntimeError("claim expiry is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise SessionRuntimeError("claim expiry must include timezone")
    return result.astimezone(timezone.utc)


def next_action(
    plan: Mapping[str, Any], status: Mapping[str, Any], session_id: str,
    *, now: datetime | None = None,
) -> dict[str, Any]:
    """Derive one next action, without attempting external work or mutation."""
    _validate_plan(plan)
    matching = [entry for entry in plan["sessions"] if entry["session_id"] == session_id]
    if not matching:
        raise SessionRuntimeError("session_id does not belong to this dispatch")
    if status.get("book_slug") != plan["book_slug"]:
        raise SessionRuntimeError("dispatch targets a different book")
    base: dict[str, Any] = {
        "schema_version": 1, "book_slug": plan["book_slug"],
        "dispatch_id": plan["dispatch_id"], "session_id": session_id,
    }

    def result(action: str, **details: Any) -> dict[str, Any]:
        return {**base, "action": action, **details}

    if not status.get("valid"):
        return result("blocked", reason="preflight_failed", errors=list(status.get("errors") or []))
    if status.get("finalization", {}).get("active"):
        return result("blocked", reason="finalization_active")
    pack = matching[0]
    context = pack["context"]
    if context["workflow_revision"] != status.get("workflow_revision"):
        return result("blocked", reason="workflow_revision_changed")
    revisions = status.get("state_revisions")
    if not isinstance(revisions, Mapping) or any(
        revisions.get(key) != context["shared_state_revisions"][key]
        for key in ("glossary", "style_guide")
    ):
        return result("blocked", reason="shared_state_changed", instruction="Replan after orchestrator reconciliation")

    current_units = {unit["unit_id"]: unit for unit in status.get("units", [])}
    claims = {claim["unit_id"]: claim for claim in status.get("claims", [])}
    clock = _now_utc(now)
    finished = 0
    waiting: list[dict[str, Any]] = []
    for assigned in pack["units"]:
        unit_id, role = assigned["unit_id"], assigned["role"]
        current = current_units.get(unit_id)
        if not isinstance(current, Mapping):
            return result("blocked", reason="unit_missing_or_source_revised", unit_id=unit_id)
        chapter_number = current.get("chapter_number")
        if current["lifecycle"] == "reviewed":
            finished += 1
            continue
        if current["lifecycle"] == "pending":
            return result("blocked", reason="unit_not_extracted", unit_id=unit_id)
        if current["lifecycle"] == "translated" and current["review"] == "pass":
            waiting.append({"unit_id": unit_id, "reason": "await_accept_review"})
            continue
        if current["lifecycle"] == "extracted":
            operation, required_role = "translate", "translator"
        elif current["lifecycle"] == "translated" and current["review"] == "corrections_required":
            operation, required_role = "correct_translation", "translator"
        elif current["lifecycle"] == "translated" and current["review"] in {"missing", "stale"}:
            operation, required_role = "review", "reviewer"
        else:
            return result("blocked", reason="invalid_unit_state", unit_id=unit_id)
        if role != required_role:
            waiting.append({"unit_id": unit_id, "reason": "await_" + required_role})
            continue
        claim = claims.get(unit_id)
        if claim is not None:
            if claim.get("session_id") != session_id:
                waiting.append({"unit_id": unit_id, "reason": "claimed_by_other_session"})
                continue
            if claim.get("role") != role:
                return result("blocked", reason="claim_role_mismatch", unit_id=unit_id)
            if _expiry(claim.get("expires_at")) <= clock:
                return result("blocked", reason="claim_expired", unit_id=unit_id,
                              instruction="Inspect and clean up the expired claim; reacquire before any write")
            return result("work", operation=operation, unit_id=unit_id,
                          chapter_number=chapter_number, role=role,
                          claim_expires_at=claim["expires_at"],
                          context=dict(context), completed_units=finished)
        return result("claim", operation=operation, unit_id=unit_id,
                      chapter_number=chapter_number, role=role,
                      context=dict(context), completed_units=finished)
    if finished == len(pack["units"]):
        return result("complete", completed_units=finished, assigned_units=finished)
    return result("wait", completed_units=finished,
                  assigned_units=len(pack["units"]), pending=waiting)
