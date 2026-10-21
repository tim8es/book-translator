"""Source-grounded, revisioned literary memory. No LLM calls or invented facts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

KINDS = {"term", "character", "character_voice", "relationship", "event",
         "narrative_style", "ambiguity", "translation_decision"}
STATUSES = {"accepted", "superseded"}
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
UNIT_RE = re.compile(r"^chapter-[0-9]{6}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class MemoryError(ValueError):
    """Memory item or evidence cannot be trusted."""


def canonical(data: Mapping[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()


def validate_entry(item: Any, *, source_revision: str) -> dict[str, Any]:
    keys = {"memory_id", "kind", "subject", "statement", "translation_decision",
            "evidence", "source_revision_id", "status", "supersedes"}
    if not isinstance(item, Mapping) or set(item) != keys:
        raise MemoryError("literary memory entry requires exact v1 fields")
    memory_id = item["memory_id"]
    if not isinstance(memory_id, str) or not ID_RE.fullmatch(memory_id):
        raise MemoryError("invalid literary memory_id")
    if item["kind"] not in KINDS or item["status"] not in STATUSES:
        raise MemoryError("invalid literary memory kind/status")
    if item["source_revision_id"] != source_revision:
        raise MemoryError("memory evidence belongs to another source revision")
    for key in ("subject", "statement"):
        if not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > 2000:
            raise MemoryError(f"memory {key} must be nonempty, bounded text")
    decision = item["translation_decision"]
    if decision is not None and (not isinstance(decision, str) or not decision.strip() or len(decision) > 1000):
        raise MemoryError("invalid translation_decision")
    supersedes = item["supersedes"]
    if supersedes is not None and (not isinstance(supersedes, str) or not ID_RE.fullmatch(supersedes)):
        raise MemoryError("invalid supersedes reference")
    source = item["evidence"]
    if not isinstance(source, Mapping) or set(source) != {"unit_id", "quote"}:
        raise MemoryError("evidence requires unit_id and verbatim source quote")
    if (not isinstance(source["unit_id"], str) or not UNIT_RE.fullmatch(source["unit_id"])
            or not isinstance(source["quote"], str) or not source["quote"].strip()
            or len(source["quote"]) > 300):
        raise MemoryError("invalid source evidence")
    return json.loads(canonical(dict(item)))


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "schema_version", "revision", "source_revision_id", "entries",
        "glossary_revision", "style_guide_revision"
    }:
        raise MemoryError("invalid memory snapshot fields")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise MemoryError("unsupported memory snapshot schema")
    if not isinstance(snapshot["source_revision_id"], str) or not snapshot["source_revision_id"]:
        raise MemoryError("source revision required for literary memory")
    if not isinstance(snapshot["entries"], list) or len(snapshot["entries"]) > 5000:
        raise MemoryError("memory snapshot requires bounded entries")
    for key in ("glossary_revision", "style_guide_revision"):
        if not isinstance(snapshot[key], str) or not snapshot[key]:
            raise MemoryError(f"{key} is required")
    entries = [validate_entry(e, source_revision=snapshot["source_revision_id"])
               for e in snapshot["entries"]]
    ids = [entry["memory_id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise MemoryError("duplicate literary memory ID")
    expected = digest({k: snapshot[k] for k in snapshot if k != "revision"})
    if snapshot["revision"] != expected:
        raise MemoryError("memory revision checksum mismatch")
    return json.loads(canonical(dict(snapshot)))


def snapshot_from(entries: Sequence[Mapping[str, Any]], *,
                  source_revision_id: str, glossary_revision: str,
                  style_guide_revision: str) -> dict[str, Any]:
    core = {
        "schema_version": 1, "source_revision_id": source_revision_id,
        "entries": sorted((dict(entry) for entry in entries), key=lambda e: e["memory_id"]),
        "glossary_revision": glossary_revision,
        "style_guide_revision": style_guide_revision,
    }
    return validate_snapshot({**core, "revision": digest(core)})


def apply_decisions(previous: Mapping[str, Any],
                    accepted: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply source-grounded entries; a replaced item becomes superseded, not erased."""
    original = validate_snapshot(previous)
    entries = {entry["memory_id"]: dict(entry) for entry in original["entries"]}
    for raw in accepted:
        candidate = validate_entry(raw, source_revision=original["source_revision_id"])
        mid = candidate["memory_id"]
        if mid in entries:
            if entries[mid] == candidate:
                continue
            raise MemoryError(f"memory ID {mid} already has different content")
        old_id = candidate["supersedes"]
        if old_id is not None:
            if old_id not in entries or entries[old_id]["status"] != "accepted":
                raise MemoryError(f"superseded memory {old_id} is absent or inactive")
            if entries[old_id]["kind"] != candidate["kind"]:
                raise MemoryError("cannot supersede a different memory kind")
            entries[old_id]["status"] = "superseded"
        entries[mid] = candidate
    return snapshot_from(list(entries.values()),
                         source_revision_id=original["source_revision_id"],
                         glossary_revision=original["glossary_revision"],
                         style_guide_revision=original["style_guide_revision"])


def context_pack(snapshot: Mapping[str, Any], *, unit_id: str,
                 source: bytes, budget_chars: int = 6000,
                 previous_units: Sequence[str] = ()) -> dict[str, Any]:
    """Bounded stable retrieval, avoiding future-chapter spoilers by default."""
    snap = validate_snapshot(snapshot)
    if not UNIT_RE.fullmatch(unit_id) or type(budget_chars) is not int or budget_chars < 256:
        raise MemoryError("invalid target unit or memory budget")
    try:
        content = source.decode("utf-8")
    except UnicodeError as exc:
        raise MemoryError("source must be valid UTF-8") from exc
    permitted = set(previous_units) | {unit_id}
    ranked = []
    for entry in snap["entries"]:
        if entry["status"] != "accepted":
            continue
        evidence = entry["evidence"]
        if evidence["unit_id"] not in permitted:
            continue
        subject_found = entry["subject"].casefold() in content.casefold()
        if evidence["unit_id"] == unit_id or subject_found or entry["kind"] in {"narrative_style", "term"}:
            priority = (0 if evidence["unit_id"] == unit_id else
                        1 if subject_found else 2)
            ranked.append((priority, entry["memory_id"], entry))
    selected = []
    size = 0
    for _, _, entry in sorted(ranked):
        cost = len(canonical(entry).decode("utf-8"))
        if size + cost > budget_chars:
            continue
        selected.append(entry)
        size += cost
    return {
        "schema_version": 1, "unit_id": unit_id,
        "memory_revision": snap["revision"],
        "entries": selected, "included": len(selected),
        "candidate_count": len(ranked), "budget_chars": budget_chars,
        "truncated": len(selected) < len(ranked),
    }


def impact_candidates(new_entries: Sequence[Mapping[str, Any]],
                      extracted: Mapping[str, bytes],
                      translated: Mapping[str, bytes]) -> list[dict[str, Any]]:
    """Conservative textual candidate detection; NOT a semantic consistency judgment."""
    impacts = []
    for entry in new_entries:
        if entry["status"] != "accepted":
            continue
        tokens = [entry["subject"]]
        if entry["translation_decision"]:
            tokens.append(entry["translation_decision"])
        for unit_id in sorted(extracted):
            try:
                src = extracted[unit_id].decode("utf-8")
                dst = translated.get(unit_id, b"").decode("utf-8")
            except UnicodeError as exc:
                raise MemoryError("impact texts must be valid UTF-8") from exc
            found = [token for token in tokens if len(token.strip()) > 2
                     and (token.casefold() in src.casefold() or token.casefold() in dst.casefold())]
            if found and unit_id != entry["evidence"]["unit_id"]:
                impacts.append({
                    "memory_id": entry["memory_id"], "unit_id": unit_id,
                    "reason": "candidate_text_match",
                    "matched_terms": sorted(set(found)), "state": "pending",
                })
    return impacts
