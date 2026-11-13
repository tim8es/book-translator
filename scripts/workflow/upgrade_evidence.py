"""Explicit, hash-bound reuse of historical evidence across an approved workflow upgrade.

This is a read-only compatibility bridge, not a new Translator acceptance or a
new independent Reviewer PASS. It never changes old claims, records, or bytes.
An operator writes the book-local bridge only after inspecting the immutable
pre-upgrade progress and ledger. Missing or mismatching evidence fails closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .storage import StorageBackend, StorageNotFound

BRIDGE_PATH = ".workflow/workflow-upgrade-evidence.json"
_SHA = re.compile(r"^[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_UNIT = re.compile(r"^chapter-[0-9]{6}$")
_ID = re.compile(r"^[a-f0-9]{32}$")
_MAX_BRIDGE_BYTES = 1024 * 1024


class EvidenceUpgradeError(ValueError):
    """A declared workflow upgrade is malformed or its proofs do not match."""


def fingerprint(value: Any) -> str:
    """Canonical digest of a historical object; independent of JSON indentation."""
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    for key, value in pairs:
        if key in doc:
            raise EvidenceUpgradeError("duplicate key in workflow upgrade evidence")
        doc[key] = value
    return doc


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def read_upgrade_bridge(
    storage: StorageBackend, metadata: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Read only the book's explicitly pinned, audited compatibility scope."""
    try:
        content = storage.read(BRIDGE_PATH).content
    except StorageNotFound:
        return None
    if len(content) > _MAX_BRIDGE_BYTES:
        raise EvidenceUpgradeError("workflow upgrade evidence is too large")
    try:
        data = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise EvidenceUpgradeError("workflow upgrade evidence is not valid JSON") from exc
    required = {
        "schema_version", "kind", "book_slug", "source_workflow_revision",
        "target_workflow_revision", "source_book_sha256",
        "pre_upgrade_commit", "pre_upgrade_progress_blob_sha",
        "pre_upgrade_review_ledger_blob_sha", "entries",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise EvidenceUpgradeError("workflow upgrade evidence has an unexpected shape")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise EvidenceUpgradeError("unsupported workflow upgrade evidence version")
    if data["kind"] != "adopt-unchanged-historical-translation-and-review":
        raise EvidenceUpgradeError("unsupported workflow upgrade kind")
    workflow = metadata.get("workflow")
    target = workflow.get("resolved_revision") if isinstance(workflow, Mapping) else None
    old, new = data["source_workflow_revision"], data["target_workflow_revision"]
    if not (_COMMIT.fullmatch(old or "") and _COMMIT.fullmatch(new or "")
            and old != new and new == target):
        raise EvidenceUpgradeError("workflow upgrade revision does not match the pinned book")
    meta_source = metadata.get("source")
    sealed = meta_source.get("sha256") if isinstance(meta_source, Mapping) else None
    if not _valid_sha(data["source_book_sha256"]) or sealed != data["source_book_sha256"]:
        raise EvidenceUpgradeError("workflow upgrade source identity is stale")
    if (not _COMMIT.fullmatch(data["pre_upgrade_commit"] or "")
            or not _COMMIT.fullmatch(data["pre_upgrade_progress_blob_sha"] or "")
            or not _COMMIT.fullmatch(data["pre_upgrade_review_ledger_blob_sha"] or "")):
        raise EvidenceUpgradeError("workflow upgrade baseline Git identity is invalid")
    if (data["book_slug"] != metadata.get("book_slug")
            and data["book_slug"] != metadata.get("slug")):
        # Metadata often does not contain a slug. The accepted unit identities
        # and original source checksum remain the cross-document constraints.
        if not isinstance(data["book_slug"], str) or not data["book_slug"]:
            raise EvidenceUpgradeError("workflow upgrade book identity is invalid")
    entries = data["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10000:
        raise EvidenceUpgradeError("workflow upgrade entries are invalid")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "unit_id", "source_sha256", "translation_sha256",
            "acceptance_fingerprint", "pass_record_id", "pass_record_fingerprint",
        }:
            raise EvidenceUpgradeError("workflow upgrade unit evidence has an unexpected shape")
        unit_id = entry["unit_id"]
        if not isinstance(unit_id, str) or not _UNIT.fullmatch(unit_id) or unit_id in seen:
            raise EvidenceUpgradeError("duplicate or malformed workflow upgrade unit")
        seen.add(unit_id)
        if not all(_valid_sha(entry[k]) for k in (
            "source_sha256", "translation_sha256", "acceptance_fingerprint",
        )):
            raise EvidenceUpgradeError("invalid workflow upgrade artifact fingerprint")
        record_id, record_hash = entry["pass_record_id"], entry["pass_record_fingerprint"]
        if ((record_id is None) != (record_hash is None)
                or record_id is not None and (
                    not isinstance(record_id, str) or not _ID.fullmatch(record_id)
                    or not _valid_sha(record_hash)
                )):
            raise EvidenceUpgradeError("invalid workflow upgrade PASS reference")
    return data


def adopted_acceptance(
    storage: StorageBackend,
    metadata: Mapping[str, Any],
    chapter: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Authorize an OLD acceptance only for exact original bytes and target pin."""
    document = read_upgrade_bridge(storage, metadata)
    if document is None:
        return None
    unit_id = chapter.get("unit_id")
    evidence = chapter.get("translation_acceptance")
    if not isinstance(evidence, Mapping):
        return None
    for entry in document["entries"]:
        if entry["unit_id"] != unit_id:
            continue
        if (evidence.get("workflow_revision") != document["source_workflow_revision"]
                or evidence.get("unit_id") != unit_id
                or evidence.get("role") != "translator"
                or evidence.get("source_sha256") != entry["source_sha256"]
                or evidence.get("translation_sha256") != entry["translation_sha256"]
                or fingerprint(evidence) != entry["acceptance_fingerprint"]):
            return None
        return entry
    return None


def adopted_pass(
    storage: StorageBackend,
    metadata: Mapping[str, Any],
    chapter: Mapping[str, Any],
    history: list[dict[str, Any]],
    source_sha256: str,
    translation_sha256: str,
) -> dict[str, Any] | None:
    """Return the ORIGINAL PASS, never a reconstructed/new-revision review."""
    entry = adopted_acceptance(storage, metadata, chapter)
    if (entry is None or not history or entry["pass_record_id"] is None
            or entry["source_sha256"] != source_sha256
            or entry["translation_sha256"] != translation_sha256):
        return None
    original = history[-1]
    bridge = read_upgrade_bridge(storage, metadata)
    assert bridge is not None
    if (original.get("record_id") != entry["pass_record_id"]
            or original.get("outcome") != "PASS"
            or original.get("workflow_revision") != bridge["source_workflow_revision"]
            or original.get("review_contract_revision") != (
                "docs/TRANSLATION.md@" + bridge["source_workflow_revision"]
            )
            or original.get("unit_id") != chapter.get("unit_id")
            or original.get("source_sha256") != source_sha256
            or original.get("translation_sha256") != translation_sha256
            or fingerprint(original) != entry["pass_record_fingerprint"]):
        return None
    return original
