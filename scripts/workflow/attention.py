"""Canonical, backend-neutral queue of reviewed units requiring human attention.

This is not a chapter lifecycle status and never grants review PASS or a build
exception. Every queued entry is bound to the exact reviewed artifact and
reviewer record; changing either makes the entry inactive automatically.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .storage import StorageNotFound

ATTENTION_PATH = ".workflow/needs-attention.json"
ATTENTION_REASONS = frozenset({
    "correction-limit", "review-round-limit", "model-anchor-retries",
    "operator-attention",
})
_ID = re.compile(r"^chapter-[0-9]{6}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RECORD = re.compile(r"^[0-9a-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
ENTRY_KEYS = {"unit_id", "source_sha256", "translation_sha256",
              "review_record_id", "reason", "created_at"}


class AttentionError(ValueError):
    """Fail closed on inconsistent persisted attention state."""


def _timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_attention(document: object, workflow_revision: str) -> dict:
    if (not isinstance(document, dict)
            or set(document) != {"schema_version", "workflow_revision", "entries"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or not isinstance(workflow_revision, str)
            or _REVISION.fullmatch(workflow_revision) is None
            or document["workflow_revision"] != workflow_revision
            or not isinstance(document["entries"], list)
            or len(document["entries"]) > 2000):
        raise AttentionError("invalid or revision-mismatched needs-attention queue")
    seen = set()
    for item in document["entries"]:
        if (not isinstance(item, dict) or set(item) != ENTRY_KEYS
                or not isinstance(item["unit_id"], str)
                or not _ID.fullmatch(item["unit_id"])
                or item["unit_id"] in seen
                or any(not isinstance(item[k], str)
                       or _HASH.fullmatch(item[k]) is None
                       for k in ("source_sha256", "translation_sha256"))
                or not isinstance(item["review_record_id"], str)
                or _RECORD.fullmatch(item["review_record_id"]) is None
                or item["reason"] not in ATTENTION_REASONS
                or not _timestamp(item["created_at"])):
            raise AttentionError("invalid needs-attention entry")
        seen.add(item["unit_id"])
    return document


def active_attention(entries: Sequence[Mapping], resolutions: Sequence,
                     chapters: Sequence[Mapping]) -> list[str]:
    """Return active exact-artifact deferred units in book reading order."""
    by_id = {item["unit_id"]: item for item in entries}
    by_resolution = {r.unit_id: r for r in resolutions}
    result = []
    for chapter in sorted(chapters, key=lambda c: c["number"]):
        unit_id = chapter.get("unit_id")
        entry = by_id.get(unit_id)
        resolution = by_resolution.get(unit_id)
        if not entry or not resolution or chapter.get("status") != "translated":
            continue
        record = resolution.current_record
        if (resolution.state == "corrections_required"
                and record is not None
                and record.get("record_id") == entry["review_record_id"]
                and resolution.source_sha256 == entry["source_sha256"]
                and resolution.translation_sha256 == entry["translation_sha256"]):
            result.append(unit_id)
    return result


class AttentionManager:
    def __init__(self, repository):
        self.repository = repository

    def load(self, workflow_revision: str) -> tuple[dict, str | None]:
        try:
            stored = self.repository.storage.read(ATTENTION_PATH)
        except StorageNotFound:
            return {"schema_version": 1, "workflow_revision": workflow_revision,
                    "entries": []}, None
        try:
            raw = json.loads(stored.content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise AttentionError("needs-attention document is not valid JSON/UTF-8") from exc
        return validate_attention(raw, workflow_revision), stored.version

    def defer(self, workflow_revision: str, resolution, *, reason: str) -> dict:
        """CAS write only an independently reviewed, currently rejected artifact."""
        if reason not in ATTENTION_REASONS:
            raise AttentionError("unsupported attention reason")
        record = resolution.current_record
        if (resolution.state != "corrections_required"
                or not isinstance(record, dict)
                or record.get("outcome") != "CORRECTIONS_REQUIRED"
                or not _RECORD.fullmatch(str(record.get("record_id", "")))
                or not _ID.fullmatch(str(resolution.unit_id))
                or not _HASH.fullmatch(str(resolution.source_sha256))
                or not _HASH.fullmatch(str(resolution.translation_sha256))):
            raise AttentionError("only a current CORRECTIONS_REQUIRED review can be deferred")
        document, previous_version = self.load(workflow_revision)
        entry = {
            "unit_id": resolution.unit_id,
            "source_sha256": resolution.source_sha256,
            "translation_sha256": resolution.translation_sha256,
            "review_record_id": record["record_id"],
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        previous = next((i for i in document["entries"]
                         if i["unit_id"] == entry["unit_id"]), None)
        if previous is not None and all(
                previous.get(k) == entry[k] for k in ENTRY_KEYS if k != "created_at"):
            return previous
        document["entries"] = [i for i in document["entries"]
                               if i["unit_id"] != entry["unit_id"]] + [entry]
        content = (json.dumps(validate_attention(document, workflow_revision),
                              ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if previous_version is None:
            self.repository.storage.create_if_absent(ATTENTION_PATH, content)
        else:
            self.repository.storage.write_if_version(
                ATTENTION_PATH, content, previous_version)
        return entry
