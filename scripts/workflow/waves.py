"""One-active-wave coordination, durable memory proposals and safe recovery.

This state machine never duplicates chapter progress. It uses the existing
StorageBackend contract (including GitHub CAS) and recomputes review readiness
from authoritative status. Publication is recoverable and never silent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .literary_memory import (
    MemoryError, canonical, context_pack, impact_candidates, snapshot_from,
    apply_decisions, validate_entry, validate_snapshot,
)
from .session_planner import plan_sessions
from .session_runtime import save_dispatch, next_action, load_dispatch
from .storage import StorageBackend, StorageNotFound, StorageAlreadyExists, StorageVersionConflict, StorageError
from .coordination import BookCoordinationManager, CoordinationError
from .repository import WorkflowStateRepository

ACTIVE = ".workflow/waves/active.json"
MEMORY_ACTIVE = ".workflow/literary-memory/active.json"
IMPACT = ".workflow/literary-memory/impact-queue.json"
HEX = re.compile(r"^[0-9a-f]{32}$")
UNIT = re.compile(r"^chapter-[0-9]{6}$")


class WaveError(RuntimeError):
    """Wave transition cannot safely continue."""


def _json_read(storage: StorageBackend, path: str) -> tuple[dict[str, Any], str]:
    try:
        value = storage.read(path)
        data = json.loads(value.content.decode("utf-8"))
    except (StorageNotFound, StorageError, ValueError, UnicodeError) as exc:
        raise WaveError(f"missing or invalid {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WaveError(f"{path} must contain an object")
    return data, value.version


def _create(storage: StorageBackend, path: str, data: Mapping[str, Any]) -> str:
    content = canonical(data)
    try:
        return storage.create_if_absent(path, content)
    except StorageAlreadyExists as exc:
        old, _ = _json_read(storage, path)
        if canonical(old) != content:
            raise WaveError(f"{path} is already present with different content") from exc
        return storage.read(path).version
    except StorageError as exc:
        raise WaveError(f"cannot create {path}: {exc}") from exc


def _write(storage: StorageBackend, path: str, data: Mapping[str, Any], expected: str) -> str:
    try:
        return storage.write_if_version(path, canonical(data), expected)
    except StorageVersionConflict as exc:
        raise WaveError(f"{path} changed concurrently; read current state before retrying") from exc
    except StorageError as exc:
        raise WaveError(f"cannot update {path}: {exc}") from exc


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _manifest(wave_id: str) -> str:
    if not isinstance(wave_id, str) or not HEX.fullmatch(wave_id):
        raise WaveError("wave_id must be 32 lowercase hexadecimal characters")
    return f".workflow/waves/{wave_id}/manifest.json"


def _state(wave_id: str) -> str:
    _manifest(wave_id)
    return f".workflow/waves/{wave_id}/state.json"


def _proposal(wave_id: str, memory_id: str) -> str:
    if not isinstance(memory_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", memory_id):
        raise WaveError("invalid proposed literary memory ID")
    return f".workflow/waves/{wave_id}/proposals/{memory_id}.json"


def _snapshot(revision: str) -> str:
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise WaveError("invalid literary memory revision")
    return f".workflow/literary-memory/snapshots/{revision}.json"


class WaveManager:
    def __init__(self, storage: StorageBackend):
        self.storage = storage

    def active(self) -> dict[str, Any] | None:
        try:
            return _json_read(self.storage, ACTIVE)[0]
        except WaveError:
            try:
                self.storage.read(ACTIVE)
            except StorageNotFound:
                return None
            raise

    def _active_state(self) -> tuple[dict[str, Any], dict[str, Any], str]:
        pointer = self.active()
        if pointer is None:
            raise WaveError("no active wave")
        if set(pointer) != {"wave_id", "book_slug"}:
            raise WaveError("active wave pointer is malformed")
        manifest, _ = _json_read(self.storage, _manifest(pointer["wave_id"]))
        state, version = _json_read(self.storage, _state(pointer["wave_id"]))
        if manifest.get("wave_id") != pointer["wave_id"] or manifest.get("book_slug") != pointer["book_slug"]:
            raise WaveError("active wave identity does not match immutable manifest")
        if state.get("wave_id") != manifest["wave_id"]:
            raise WaveError("wave state does not match manifest")
        return manifest, state, version

    def memory(self) -> dict[str, Any]:
        pointer, _ = _json_read(self.storage, MEMORY_ACTIVE)
        snap, _ = _json_read(self.storage, _snapshot(pointer.get("revision")))
        return validate_snapshot(snap)

    def _ensure_memory(self, *, source_revision: str, shared: Mapping[str, str]) -> dict[str, Any]:
        try:
            return self.memory()
        except WaveError:
            try:
                self.storage.read(MEMORY_ACTIVE)
            except StorageNotFound:
                pass
            else:
                raise
        snap = snapshot_from([], source_revision_id=source_revision,
                             glossary_revision=shared["glossary"],
                             style_guide_revision=shared["style_guide"])
        _create(self.storage, _snapshot(snap["revision"]), snap)
        _create(self.storage, MEMORY_ACTIVE, {"revision": snap["revision"]})
        return self.memory()

    @staticmethod
    def _admit(status: Mapping[str, Any], *, book_slug: str) -> None:
        if not status.get("valid") or status.get("book_slug") != book_slug:
            raise WaveError("source/corpus/review preflight failed or book identity differs")
        if status.get("finalization", {}).get("active"):
            raise WaveError("book finalization is active")
        if status.get("claims"):
            raise WaveError("cannot create or reconcile wave while chapter claims are active")

    def plan(self, status: Mapping[str, Any], *, count: int) -> dict[str, Any]:
        if not status.get("valid"):
            raise WaveError("cannot plan with invalid workflow status")
        if self.active() is not None:
            raise WaveError("active wave already exists")
        if type(count) is not int or count < 1 or count > 100:
            raise WaveError("wave size must be 1-100")
        available = [u for u in status.get("units", []) if u["lifecycle"] != "reviewed"]
        if not available:
            raise WaveError("all units are already reviewed")
        selected = available[:count]
        if any(u["lifecycle"] == "pending" for u in selected):
            raise WaveError("selected wave contains an unextracted unit")
        return {"schema_version": 1, "book_slug": status["book_slug"],
                "unit_ids": [u["unit_id"] for u in selected],
                "chapter_numbers": [u["chapter_number"] for u in selected]}

    def start(self, status: Mapping[str, Any], metadata: Mapping[str, Any], *,
              count: int, wave_id: str | None = None) -> dict[str, Any]:
        self._admit(status, book_slug=status.get("book_slug"))
        if self.storage.list(".workflow/claims"):
            raise WaveError("cannot start wave with durable chapter claims")
        from .coordination import SOURCE_PROMOTION_PATH, FINALIZATION_PATH
        for barrier in (SOURCE_PROMOTION_PATH, FINALIZATION_PATH):
            try:
                self.storage.read(barrier)
            except StorageNotFound:
                pass
            else:
                raise WaveError(f"wave start blocked by {barrier}")
        plan = self.plan(status, count=count)
        source = metadata.get("source")
        if not isinstance(source, Mapping) or not source.get("revision_id"):
            raise WaveError("source revision identity required")
        shared = status.get("state_revisions", {})
        if any(not isinstance(shared.get(k), str) for k in ("glossary", "style_guide")):
            raise WaveError("glossary/style SHA-bound snapshots required")
        mem = self._ensure_memory(source_revision=source["revision_id"], shared=shared)
        if mem["source_revision_id"] != source["revision_id"]:
            raise WaveError("memory is bound to an older source edition; migrate explicitly")
        if (mem["glossary_revision"] != shared["glossary"]
                or mem["style_guide_revision"] != shared["style_guide"]):
            raise WaveError("shared glossary/style changed without reconciled memory")
        wave_id = wave_id or uuid4().hex
        _manifest(wave_id)
        source_sha = source.get("sha256")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
            raise WaveError("valid original source SHA-256 required")
        manifest = {
            **plan, "wave_id": wave_id,
            "source_revision_id": source["revision_id"],
            "source_sha256": source_sha,
            "workflow_revision": status["workflow_revision"],
            "memory_revision": mem["revision"],
            "shared_state_revisions": {k: shared[k] for k in ("glossary", "style_guide")},
            "created_at": _now(),
        }
        state = {"schema_version": 1, "wave_id": wave_id, "phase": "ACTIVE",
                 "decisions_sha256": None, "next_memory_revision": None}
        _create(self.storage, _manifest(wave_id), manifest)
        _create(self.storage, _state(wave_id), state)
        try:
            self.storage.create_if_absent(ACTIVE, canonical({
                "wave_id": wave_id, "book_slug": plan["book_slug"]
            }))
        except StorageAlreadyExists as exc:
            if self.active() != {"wave_id": wave_id, "book_slug": plan["book_slug"]}:
                raise WaveError("another wave started concurrently") from exc
        return manifest

    def status(self, status: Mapping[str, Any]) -> dict[str, Any]:
        pointer = self.active()
        if pointer is None:
            return {"active": False}
        manifest, state, _ = self._active_state()
        units = {u["unit_id"]: u for u in status.get("units", [])}
        pending = [u for u in manifest["unit_ids"] if not (
            u in units and units[u]["lifecycle"] == "reviewed" and units[u]["review"] == "pass"
        )]
        return {"active": True, "manifest": manifest, "state": state,
                "unreviewed_units": pending}

    def submit(self, wave_id: str, session_id: str, entry: Mapping[str, Any],
               *, source: bytes, source_revision: str) -> str:
        manifest, state, _ = self._active_state()
        if manifest["wave_id"] != wave_id or state["phase"] != "ACTIVE":
            raise WaveError("memory proposal requires an active matching wave")
        item = validate_entry(entry, source_revision=source_revision)
        if item["source_revision_id"] != manifest["source_revision_id"]:
            raise WaveError("proposal uses wrong edition")
        if item["evidence"]["unit_id"] not in manifest["unit_ids"]:
            raise WaveError("proposal evidence outside active wave")
        try:
            if item["evidence"]["quote"] not in source.decode("utf-8"):
                raise WaveError("memory evidence quote not present in original source")
        except UnicodeError as exc:
            raise WaveError("source text is not UTF-8") from exc
        path = f".workflow/claims/{item['evidence']['unit_id']}.json"
        claim, _ = _json_read(self.storage, path)
        if claim.get("session_id") != session_id or claim.get("role") not in {"translator", "reviewer"}:
            raise WaveError("only current claimed worker may propose literary memory")
        expires_at = datetime.fromisoformat(claim["expires_at"].replace("Z", "+00:00"))
        if expires_at <= datetime.now(timezone.utc):
            raise WaveError("memory proposal claim has expired")
        payload = {"schema_version": 1, "wave_id": wave_id, "session_id": session_id,
                   "entry": item}
        return _create(self.storage, _proposal(wave_id, item["memory_id"]), payload)

    def proposals(self, wave_id: str) -> list[dict[str, Any]]:
        _manifest(wave_id)
        prefix = f".workflow/waves/{wave_id}/proposals"
        return [_json_read(self.storage, path)[0] for path in self.storage.list(prefix)
                if path.endswith(".json")]

    def reconcile_preview(self, status: Mapping[str, Any],
                          *, decisions: Mapping[str, str],
                          read_source, read_translation) -> dict[str, Any]:
        manifest, state, _ = self._active_state()
        if state["phase"] not in {"ACTIVE", "RECONCILING"}:
            raise WaveError("wave cannot reconcile from its current phase")
        if not status.get("valid"):
            raise WaveError("preflight failed during wave reconciliation")
        if status.get("claims") or self.storage.list(".workflow/claims"):
            raise WaveError("active chapter claims prevent memory reconciliation")
        by_id = {u["unit_id"]: u for u in status["units"]}
        for uid in manifest["unit_ids"]:
            unit = by_id.get(uid)
            if not unit or unit["lifecycle"] != "reviewed" or unit["review"] != "pass":
                raise WaveError(f"{uid} lacks current independent PASS/accepted review")
        proposals = self.proposals(manifest["wave_id"])
        ids = {p["entry"]["memory_id"] for p in proposals}
        if not isinstance(decisions, Mapping) or set(decisions) != ids or any(
            value not in {"accepted", "rejected"} for value in decisions.values()
        ):
            raise WaveError("each submitted proposal requires accepted/rejected decision")
        previous, _ = _json_read(self.storage, _snapshot(manifest["memory_revision"]))
        accepted = []
        for proposal in proposals:
            entry = proposal["entry"]
            if decisions[entry["memory_id"]] != "accepted":
                continue
            evidence = entry["evidence"]
            target = by_id.get(evidence["unit_id"])
            if target is None or evidence["quote"] not in read_source(target["source_path"]).decode("utf-8"):
                raise WaveError("accepted memory proposal has unverifiable original quote")
            accepted.append(entry)
        next_snapshot = apply_decisions(previous, accepted)
        # Glossary/style proposals remain an independent existing contract:
        # the current wave cannot silently reconcile their text files.
        current_revisions = status.get("state_revisions", {})
        if any(current_revisions.get(k) != manifest["shared_state_revisions"][k]
               for k in ("glossary", "style_guide")):
            raise WaveError("shared glossary/style changed during wave")
        units = {uid: read_source(v["source_path"]) for uid, v in by_id.items()
                 if v["lifecycle"] == "reviewed"}
        translated = {uid: read_translation(v["translation_path"]) for uid, v in by_id.items()
                      if v["lifecycle"] == "reviewed"}
        candidates = impact_candidates(accepted, units, translated)
        return {
            "schema_version": 1, "wave_id": manifest["wave_id"],
            "decisions": dict(sorted(decisions.items())),
            "previous_memory_revision": manifest["memory_revision"],
            "next_snapshot": next_snapshot, "impact_candidates": candidates,
        }


    def prepare_close(self, status: Mapping[str, Any], *,
                      decisions: Mapping[str, str],
                      read_source, read_translation) -> dict[str, Any]:
        """Freeze the exact planned publication and enter RECONCILING (recoverable)."""
        manifest, state, version = self._active_state()
        if state["phase"] == "RECONCILING":
            frozen, _ = _json_read(self.storage, f".workflow/waves/{manifest['wave_id']}/reconciliation.json")
            if frozen["decisions"] != dict(sorted(decisions.items())):
                raise WaveError("reconciling wave already has another frozen decision set")
            return frozen
        if state["phase"] != "ACTIVE":
            raise WaveError("wave must be ACTIVE before reconciliation")
        preview = self.reconcile_preview(
            status, decisions=decisions, read_source=read_source,
            read_translation=read_translation,
        )
        path = f".workflow/waves/{manifest['wave_id']}/reconciliation.json"
        _create(self.storage, path, preview)
        _write(self.storage, _state(manifest["wave_id"]), {
            **state, "phase": "RECONCILING",
            "decisions_sha256": __import__("hashlib").sha256(canonical(preview["decisions"])).hexdigest(),
            "next_memory_revision": preview["next_snapshot"]["revision"],
        }, version)
        return preview

    def close(self, *, expected_wave: str) -> dict[str, Any]:
        """Idempotent ordered publication, with visible RECONCILING barrier."""
        manifest, state, state_version = self._active_state()
        if expected_wave != manifest["wave_id"] or state["phase"] != "RECONCILING":
            raise WaveError("wave must match active reconciling manifest")
        path = f".workflow/waves/{expected_wave}/reconciliation.json"
        frozen, _ = _json_read(self.storage, path)
        if frozen["previous_memory_revision"] != manifest["memory_revision"]:
            raise WaveError("frozen reconciliation uses another memory revision")
        next_snapshot = validate_snapshot(frozen["next_snapshot"])
        if next_snapshot["revision"] != state["next_memory_revision"]:
            raise WaveError("reconciliation plan differs from pinned wave state")
        pointer, pointer_version = _json_read(self.storage, MEMORY_ACTIVE)
        if pointer["revision"] not in {manifest["memory_revision"], next_snapshot["revision"]}:
            raise WaveError("memory changed outside the current wave")

        # Publish immutable artifacts before switching the authoritative pointer.
        _create(self.storage, _snapshot(next_snapshot["revision"]), next_snapshot)
        impacts = {
            "schema_version": 1, "wave_id": expected_wave,
            "memory_revision": next_snapshot["revision"],
            "candidates": frozen["impact_candidates"],
        }
        _create(self.storage, f".workflow/literary-memory/impacts/{expected_wave}.json", impacts)
        if pointer["revision"] != next_snapshot["revision"]:
            _write(self.storage, MEMORY_ACTIVE, {"revision": next_snapshot["revision"]},
                   pointer_version)

        state = {**state, "phase": "CLOSED"}
        _write(self.storage, _state(expected_wave), state, state_version)
        try:
            active = self.storage.read(ACTIVE)
            pointer_data = json.loads(active.content.decode("utf-8"))
            if pointer_data["wave_id"] != expected_wave:
                raise WaveError("active wave changed before closure")
            self.storage.delete_if_version(ACTIVE, active.version)
        except StorageNotFound:
            pass
        except StorageVersionConflict as exc:
            raise WaveError("active pointer changed before closure; recover") from exc
        return {"state": "CLOSED", "wave_id": expected_wave,
                "memory_revision": next_snapshot["revision"],
                "impact_count": len(impacts["candidates"])}

    def recover(self, *, wave_id: str) -> dict[str, Any]:
        """Do not manufacture reconciliation plans; finish an existing frozen transition."""
        manifest, state, _ = self._active_state()
        if manifest["wave_id"] != wave_id:
            raise WaveError("requested wave is not active")
        if state["phase"] != "RECONCILING":
            raise WaveError("no recoverable reconciliation in progress")
        return self.close(expected_wave=wave_id)

    def impact_queue(self) -> list[dict[str, Any]]:
        """All unresolved historical impact references must be explicitly reviewed."""
        results = []
        prefix = ".workflow/literary-memory/impacts"
        for path in self.storage.list(prefix):
            if not path.endswith(".json"):
                continue
            doc, _ = _json_read(self.storage, path)
            for impact in doc.get("candidates", []):
                key = f"{impact['memory_id']}-{impact['unit_id']}.json"
                resolution_path = (
                    f".workflow/literary-memory/resolutions/{doc['wave_id']}/{key}"
                )
                try:
                    resolved, _ = _json_read(self.storage, resolution_path)
                except WaveError:
                    try:
                        self.storage.read(resolution_path)
                    except StorageNotFound:
                        results.append({**impact, "wave_id": doc["wave_id"]})
                        continue
                    raise
                if resolved.get("reviewed") is not True:
                    results.append({**impact, "wave_id": doc["wave_id"]})
        return results

    def resolve_impact(self, *, wave_id: str, memory_id: str, unit_id: str,
                       review_record: Mapping[str, Any],
                       current_translation_sha256: str,
                       reviewer_claim: Mapping[str, Any]) -> str:
        doc, _ = _json_read(self.storage, f".workflow/literary-memory/impacts/{wave_id}.json")
        if not any(
            x["memory_id"] == memory_id and x["unit_id"] == unit_id
            for x in doc["candidates"]
        ):
            raise WaveError("impact candidate is not recorded in this wave")
        if review_record.get("outcome") != "PASS" or review_record.get("unit_id") != unit_id:
            raise WaveError("impact resolution needs independent current PASS record")
        if review_record.get("translation_sha256") != current_translation_sha256:
            raise WaveError("PASS review references another translation artifact")
        if reviewer_claim.get("role") != "reviewer" or reviewer_claim.get("unit_id") != unit_id:
            raise WaveError("impact resolution requires a reviewer claim for this unit")
        if reviewer_claim.get("session_id") != review_record.get("reviewer_session_id"):
            raise WaveError("reviewer claim and accepted reviewer record disagree")
        expires = datetime.fromisoformat(reviewer_claim["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise WaveError("reviewer claim expired before impact resolution")
        reviewed = datetime.fromisoformat(review_record["reviewed_at"].replace("Z", "+00:00"))
        manifest, _ = _json_read(self.storage, _manifest(wave_id))
        created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
        if reviewed <= created:
            raise WaveError("impact re-review must be newer than the wave")
        path = f".workflow/literary-memory/resolutions/{wave_id}/{memory_id}-{unit_id}.json"
        data = {
            "schema_version": 1, "wave_id": wave_id, "memory_id": memory_id,
            "unit_id": unit_id, "reviewed": True,
            "review_record_id": review_record["record_id"],
            "translation_sha256": current_translation_sha256,
        }
        return _create(self.storage, path, data)
