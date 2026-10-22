"""One-active-wave coordination, durable memory proposals and safe recovery.

This state machine never duplicates chapter progress. It uses the existing
StorageBackend contract (including GitHub CAS) and recomputes review readiness
from authoritative status. Publication is recoverable and never silent.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Callable
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
from .schemas import SchemaKind
from .claims import unit_id_for_chapter
from .reviews import ReviewLedgerManager

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



def _reviewed_artifact_seal(
    status: Mapping[str, Any],
    manifest: Mapping[str, Any],
    read_source: Callable[[str], bytes],
    read_translation: Callable[[str], bytes],
    *,
    originals: Mapping[str, bytes] | None = None,
    translations: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Pin the exact review inputs and all chapter bytes used by impact analysis.

    The seal is re-evaluated from durable state while holding the book's
    coordination lease immediately before publishing the memory pointer.
    """
    if not status.get("valid") or status.get("book_slug") != manifest["book_slug"]:
        raise WaveError("wave publication requires a valid preflight for the pinned book")
    if status.get("finalization", {}).get("active") or status.get("claims"):
        raise WaveError("wave publication blocked by finalization or active claims")
    if status.get("workflow_revision") != manifest["workflow_revision"]:
        raise WaveError("workflow revision changed since wave admission")
    corpus = status.get("corpus")
    if not isinstance(corpus, Mapping) or corpus.get("source_sha256") != manifest["source_sha256"]:
        raise WaveError("original source fingerprint changed since wave admission")
    revisions = status.get("state_revisions")
    required = ("metadata", "progress", "review_ledger", "glossary", "style_guide")
    if not isinstance(revisions, Mapping) or any(
        not isinstance(revisions.get(key), str) or not revisions[key] for key in required
    ):
        raise WaveError("wave publication requires verifiable authoritative state revisions")
    if any(revisions[key] != manifest["shared_state_revisions"][key]
           for key in ("glossary", "style_guide")):
        raise WaveError("glossary/style changed since wave admission")
    units = status.get("units")
    if not isinstance(units, list):
        raise WaveError("wave publication requires the verified unit listing")
    by_id = {unit["unit_id"]: unit for unit in units}
    if len(by_id) != len(units):
        raise WaveError("duplicate unit IDs in wave publication state")
    for uid in manifest["unit_ids"]:
        unit = by_id.get(uid)
        if not unit or unit["lifecycle"] != "reviewed" or unit["review"] != "pass":
            raise WaveError(f"{uid} has no current accepted independent PASS")
    reviewed = []
    for uid in sorted(by_id):
        unit = by_id[uid]
        if unit["lifecycle"] != "reviewed":
            continue
        if unit["review"] != "pass":
            raise WaveError(f"{uid} is reviewed without current PASS")
        original = (originals[uid] if originals is not None and uid in originals
                    else read_source(unit["source_path"]))
        translation = (translations[uid] if translations is not None and uid in translations
                       else read_translation(unit["translation_path"]))
        reviewed.append({
            "unit_id": uid, "chapter_number": unit["chapter_number"],
            "source_path": unit["source_path"],
            "translation_path": unit["translation_path"],
            "source_sha256": hashlib.sha256(original).hexdigest(),
            "translation_sha256": hashlib.sha256(translation).hexdigest(),
        })
    return {
        "schema_version": 1,
        "book_slug": manifest["book_slug"],
        "workflow_revision": status["workflow_revision"],
        "source_sha256": corpus["source_sha256"],
        "state_revisions": {key: revisions[key] for key in required},
        "reviewed_artifacts": reviewed,
    }


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
        coordinator = BookCoordinationManager(WorkflowStateRepository(self.storage))
        try:
            lease = coordinator.acquire(operation="wave_transition", session_id="wave-start")
        except CoordinationError as exc:
            raise WaveError(f"wave transition cannot acquire coordination: {exc}") from exc
        try:
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
        finally:
            try:
                coordinator.release(lease)
            except CoordinationError as exc:
                raise WaveError(f"wave transition could not release coordination: {exc}") from exc

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
        coordinator = BookCoordinationManager(WorkflowStateRepository(self.storage))
        try:
            lease = coordinator.acquire(operation="wave_transition", session_id="wave-submit")
        except CoordinationError as exc:
            raise WaveError(f"cannot acquire wave transition guard: {exc}") from exc
        try:
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
        finally:
            try:
                coordinator.release(lease)
            except CoordinationError as exc:
                raise WaveError(f"wave transition guard release failed: {exc}") from exc

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
        seal = _reviewed_artifact_seal(
            status, manifest, read_source, read_translation,
            originals=units, translations=translated,
        )
        return {
            "schema_version": 1, "wave_id": manifest["wave_id"],
            "publication_seal": seal,
            "decisions": dict(sorted(decisions.items())),
            "previous_memory_revision": manifest["memory_revision"],
            "next_snapshot": next_snapshot, "impact_candidates": candidates,
        }


    def prepare_close(self, status: Mapping[str, Any], *,
                      decisions: Mapping[str, str],
                      read_source, read_translation,
                      fresh_status: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
        coordinator = BookCoordinationManager(WorkflowStateRepository(self.storage))
        try:
            lease = coordinator.acquire(operation="wave_transition", session_id="wave-prepare_close")
        except CoordinationError as exc:
            raise WaveError(f"cannot acquire wave transition guard: {exc}") from exc
        try:
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
                fresh_status(), decisions=decisions, read_source=read_source,
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
        finally:
            try:
                coordinator.release(lease)
            except CoordinationError as exc:
                raise WaveError(f"wave transition guard release failed: {exc}") from exc

    def close(self, *, expected_wave: str,
              fresh_status: Callable[[], Mapping[str, Any]],
              read_source: Callable[[str], bytes],
              read_translation: Callable[[str], bytes]) -> dict[str, Any]:
        coordinator = BookCoordinationManager(WorkflowStateRepository(self.storage))
        try:
            lease = coordinator.acquire(operation="wave_transition", session_id="wave-close")
        except CoordinationError as exc:
            raise WaveError(f"wave transition cannot acquire coordination: {exc}") from exc
        try:
            """Idempotent ordered publication, with visible RECONCILING barrier."""
            manifest, state, state_version = self._active_state()
            if self.storage.list(".workflow/claims"):
                raise WaveError("cannot publish while durable literary claims exist")
            if expected_wave != manifest["wave_id"] or state["phase"] not in {"RECONCILING", "CLOSED"}:
                raise WaveError("wave must match active reconciling manifest")
            path = f".workflow/waves/{expected_wave}/reconciliation.json"
            frozen, _ = _json_read(self.storage, path)
            if frozen["previous_memory_revision"] != manifest["memory_revision"]:
                raise WaveError("frozen reconciliation uses another memory revision")
            next_snapshot = validate_snapshot(frozen["next_snapshot"])
            if next_snapshot["revision"] != state["next_memory_revision"]:
                raise WaveError("reconciliation plan differs from pinned wave state")
            if state.get("decisions_sha256") != hashlib.sha256(canonical(frozen["decisions"])).hexdigest():
                raise WaveError("frozen reconciliation decisions have been modified")
            frozen_seal = frozen.get("publication_seal")
            if not isinstance(frozen_seal, Mapping):
                raise WaveError("frozen reconciliation lacks verified publication seal")
            pointer, pointer_version = _json_read(self.storage, MEMORY_ACTIVE)
            if pointer["revision"] not in {manifest["memory_revision"], next_snapshot["revision"]}:
                raise WaveError("memory changed outside the current wave")
            if state["phase"] == "CLOSED":
                # Committed publication may have been interrupted while removing
                # the active pointer. Only clean up if publication is durable.
                # Subsequent book edits do not invalidate a *committed* wave.
                if pointer["revision"] != next_snapshot["revision"]:
                    raise WaveError("CLOSED wave has no published memory revision")
            else:
                current_seal = _reviewed_artifact_seal(
                    fresh_status(), manifest, read_source, read_translation,
                )
                if current_seal != frozen_seal:
                    raise WaveError(
                        "wave inputs changed since reconciliation; do not publish stale literary memory"
                    )

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

            if state["phase"] != "CLOSED":
                state = {**state, "phase": "CLOSED", "closed_at": _now()}
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
        finally:
            try:
                coordinator.release(lease)
            except CoordinationError as exc:
                raise WaveError(f"wave transition could not release coordination: {exc}") from exc

    def recover(self, *, wave_id: str,
                fresh_status: Callable[[], Mapping[str, Any]],
                read_source: Callable[[str], bytes],
                read_translation: Callable[[str], bytes]) -> dict[str, Any]:
        """Do not manufacture reconciliation plans; finish an existing frozen transition."""
        manifest, state, _ = self._active_state()
        if manifest["wave_id"] != wave_id:
            raise WaveError("requested wave is not active")
        if state["phase"] not in {"RECONCILING", "CLOSED"}:
            raise WaveError("no recoverable reconciliation in progress")
        return self.close(
            expected_wave=wave_id, fresh_status=fresh_status,
            read_source=read_source, read_translation=read_translation,
        )

    def sync_shared_memory(self, status: Mapping[str, Any],
                           metadata: Mapping[str, Any]) -> dict[str, Any]:
        """Bind glossary/style edits reconciled between waves to a new snapshot.

        Conservatively impacts all already-reviewed units: prose changes cannot
        be interpreted safely by substring matching alone.
        """
        if self.active() is not None or not status.get("valid"):
            raise WaveError("memory synchronization requires no active wave and valid preflight")
        if status.get("claims") or self.storage.list(".workflow/claims"):
            raise WaveError("cannot synchronize literary memory while claims exist")
        source = metadata.get("source")
        if not isinstance(source, Mapping) or not source.get("revision_id"):
            raise WaveError("source identity unavailable for memory synchronization")
        shared = status.get("state_revisions")
        if not isinstance(shared, Mapping) or any(
            not isinstance(shared.get(k), str) or not shared[k]
            for k in ("glossary", "style_guide")
        ):
            raise WaveError("current glossary/style revisions are required")
        coordinator = BookCoordinationManager(WorkflowStateRepository(self.storage))
        try:
            lease = coordinator.acquire(operation="wave_transition", session_id="memory-sync")
        except CoordinationError as exc:
            raise WaveError(f"cannot synchronize during another book transition: {exc}") from exc
        try:
            pointer, pointer_version = _json_read(self.storage, MEMORY_ACTIVE)
            previous = self.memory()
            if previous["source_revision_id"] != source["revision_id"]:
                raise WaveError("memory cannot silently rebase onto another source edition")
            if (previous["glossary_revision"] == shared["glossary"]
                    and previous["style_guide_revision"] == shared["style_guide"]):
                return {"state": "unchanged", "memory_revision": previous["revision"]}
            updated = snapshot_from(
                previous["entries"], source_revision_id=previous["source_revision_id"],
                glossary_revision=shared["glossary"],
                style_guide_revision=shared["style_guide"],
            )
            import hashlib
            sync_id = hashlib.sha256(canonical({
                "old": previous["revision"], "new": updated["revision"],
            })).hexdigest()[:32]
            path = f".workflow/literary-memory/syncs/{sync_id}.json"
            try:
                event, _ = _json_read(self.storage, path)
            except WaveError:
                try:
                    self.storage.read(path)
                except StorageNotFound:
                    event = {
                        "schema_version": 1, "sync_id": sync_id,
                        "phase": "CLOSED", "closed_at": _now(),
                        "previous_memory_revision": previous["revision"],
                        "next_memory_revision": updated["revision"],
                    }
                else:
                    raise
            if event.get("next_memory_revision") != updated["revision"]:
                raise WaveError("existing memory synchronization identity differs")
            # Only strictly current reviewed chapters receive an impact item.
            reviewed = sorted(u["unit_id"] for u in status.get("units", [])
                              if u["lifecycle"] == "reviewed" and u["review"] == "pass")
            candidates = [
                {"memory_id": f"shared-{sync_id}", "unit_id": uid,
                 "reason": "shared_state_reconciled", "matched_terms": [], "state": "pending"}
                for uid in reviewed
            ]
            _create(self.storage, path, event)
            _create(self.storage, _snapshot(updated["revision"]), updated)
            _create(self.storage, f".workflow/literary-memory/impacts/{sync_id}.json", {
                "schema_version": 1, "wave_id": sync_id,
                "memory_revision": updated["revision"], "candidates": candidates,
            })
            _write(self.storage, MEMORY_ACTIVE, {"revision": updated["revision"]},
                   pointer_version)
            return {"state": "synchronized", "memory_revision": updated["revision"],
                    "impact_count": len(candidates), "sync_id": sync_id}
        finally:
            try:
                coordinator.release(lease)
            except CoordinationError as exc:
                raise WaveError(f"memory synchronization could not release lock: {exc}") from exc

    def _current_impact_reviews(self) -> dict[str, dict[str, Any]]:
        """Resolve current PASS records from authoritative progress, ledger, and bytes.

        Never trust a historical resolution by its presence alone. This method
        is only invoked when a previously resolved impact needs validation.
        """
        repository = WorkflowStateRepository(self.storage)
        try:
            progress = repository.read("progress.json", SchemaKind.PROGRESS).data
            metadata = repository.read("metadata.json", SchemaKind.METADATA).data
            reviews = ReviewLedgerManager(
                repository, artifact_reader=lambda path: self.storage.read(path).content,
            ).resolve_all(progress, metadata)
        except Exception as exc:
            raise WaveError(f"cannot revalidate literary impact against current review state: {exc}") from exc
        return {
            review.unit_id: {
                "record_id": review.current_record["record_id"],
                "translation_sha256": review.translation_sha256,
                "source_sha256": review.source_sha256,
            }
            for review in reviews
            if review.state == "pass" and review.current_record is not None
        }

    def impact_queue(self) -> list[dict[str, Any]]:
        """Return unresolved or newly stale impact checks; compare current PASS hashes."""

        results = []
        current_reviews: dict[str, dict[str, Any]] | None = None
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
                    continue
                if current_reviews is None:
                    current_reviews = self._current_impact_reviews()
                current = current_reviews.get(impact["unit_id"])
                if (current is None
                        or resolved.get("memory_revision") != doc.get("memory_revision")
                        or resolved.get("translation_sha256") != current["translation_sha256"]
                        or resolved.get("review_record_id") != current["record_id"]):
                    results.append({**impact, "wave_id": doc["wave_id"],
                                    "state": "pending", "reason": "previous_resolution_stale"})
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
        current = self._current_impact_reviews().get(unit_id)
        if (current is None or current["record_id"] != review_record.get("record_id")
                or current["translation_sha256"] != current_translation_sha256
                or current["source_sha256"] != review_record.get("source_sha256")):
            raise WaveError("impact resolution must bind current PASS and both source/translation hashes")
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
        try:
            closed, _ = _json_read(self.storage, _state(wave_id))
        except WaveError:
            try:
                self.storage.read(_state(wave_id))
            except StorageNotFound:
                closed, _ = _json_read(
                    self.storage, f".workflow/literary-memory/syncs/{wave_id}.json"
                )
            else:
                raise
        if closed.get("phase") != "CLOSED" or not closed.get("closed_at"):
            raise WaveError("impact review cannot precede memory publication")
        cutoff = datetime.fromisoformat(closed["closed_at"].replace("Z", "+00:00"))
        if reviewed <= cutoff:
            raise WaveError("impact re-review must be later than memory publication")
        quality = review_record.get("quality_report")
        if (not isinstance(quality, Mapping)
                or quality.get("evidence_version") != 2
                or quality.get("context") != "independent_session"):
            raise WaveError("impact resolution requires independent anchored v2 review")
        path = f".workflow/literary-memory/resolutions/{wave_id}/{memory_id}-{unit_id}.json"
        data = {
            "schema_version": 1, "wave_id": wave_id, "memory_id": memory_id,
            "unit_id": unit_id, "reviewed": True,
            "review_record_id": review_record["record_id"],
            "memory_revision": doc["memory_revision"],
            "translation_sha256": current_translation_sha256,
        }
        return _create(self.storage, path, data)
