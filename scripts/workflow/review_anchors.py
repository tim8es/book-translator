"""Verify source-linked literary review evidence against exact artifact bytes.

V2 evidence is strictly optional for historical compatibility, but any report
that opts in must have independently checkable source/translation anchors.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


FINDING_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
RECORD_ID = re.compile(r"^[0-9a-f]{32}$")
CATEGORIES = {
    "omission", "addition", "meaning", "modality", "referent",
    "terminology", "register", "voice", "style", "structure",
}


class EvidenceAnchorError(ValueError):
    """An evidence span is absent, ambiguous or invalid for the artifact."""


def _anchor(value: Any, path: str, *, optional: bool = False) -> dict[str, Any] | None:
    if value is None and optional:
        return None
    if not isinstance(value, Mapping) or set(value) != {"quote", "occurrence"}:
        raise EvidenceAnchorError(f"{path} requires quote and occurrence")
    quote, occurrence = value["quote"], value["occurrence"]
    if not isinstance(quote, str) or not 1 <= len(quote) <= 300 or not quote.strip():
        raise EvidenceAnchorError(f"{path}.quote must be 1-300 nonblank characters")
    if type(occurrence) is not int or not 0 <= occurrence < 10000:
        raise EvidenceAnchorError(f"{path}.occurrence must be a nonnegative integer < 10000")
    return {"quote": quote, "occurrence": occurrence}


def validate_v2_finding(finding: Any, number: int) -> dict[str, Any]:
    where = f"findings[{number}]"
    required = {
        "id", "category", "severity", "detail", "resolved",
        "source_anchor", "translation_anchor", "resolves",
    }
    if not isinstance(finding, Mapping) or set(finding) != required:
        raise EvidenceAnchorError(f"{where} must contain {', '.join(sorted(required))}")
    fid = finding["id"]
    if not isinstance(fid, str) or FINDING_ID.fullmatch(fid) is None:
        raise EvidenceAnchorError(f"{where}.id must be a short stable slug")
    category = finding["category"]
    if category not in CATEGORIES:
        raise EvidenceAnchorError(f"{where}.category is unsupported")
    severity = finding["severity"]
    if severity not in {"minor", "major", "critical"}:
        raise EvidenceAnchorError(f"{where}.severity is invalid")
    detail = finding["detail"]
    if not isinstance(detail, str) or not 1 <= len(detail.strip()) <= 1000:
        raise EvidenceAnchorError(f"{where}.detail is missing or too long")
    if type(finding["resolved"]) is not bool:
        raise EvidenceAnchorError(f"{where}.resolved must be boolean")
    source = _anchor(finding["source_anchor"], f"{where}.source_anchor")
    target = _anchor(finding["translation_anchor"], f"{where}.translation_anchor", optional=True)
    if target is None and category != "omission":
        raise EvidenceAnchorError(f"{where} missing translation anchor is allowed only for omission")
    if finding["resolved"] and target is None:
        raise EvidenceAnchorError(f"{where} resolved finding requires a current translation anchor")
    resolves = finding["resolves"]
    if resolves is not None:
        if not isinstance(resolves, Mapping) or set(resolves) != {"record_id", "finding_id"}:
            raise EvidenceAnchorError(f"{where}.resolves requires record_id and finding_id")
        if (not isinstance(resolves["record_id"], str)
                or RECORD_ID.fullmatch(resolves["record_id"]) is None
                or not isinstance(resolves["finding_id"], str)
                or FINDING_ID.fullmatch(resolves["finding_id"]) is None):
            raise EvidenceAnchorError(f"{where}.resolves has invalid identifiers")
        resolves = dict(resolves)
    return {
        "id": fid, "category": category, "severity": severity,
        "detail": detail, "resolved": finding["resolved"],
        "source_anchor": source, "translation_anchor": target, "resolves": resolves,
    }


def _check_anchor(anchor: Mapping[str, Any], text: str, where: str) -> None:
    quote, selected = anchor["quote"], anchor["occurrence"]
    matches = []
    start = 0
    while True:
        at = text.find(quote, start)
        if at < 0:
            break
        matches.append(at)
        if len(matches) > selected + 1:
            break
        start = at + len(quote)
    if len(matches) <= selected:
        raise EvidenceAnchorError(
            f"{where}: quoted passage occurrence {selected} is absent from the current artifact"
        )
    if selected == 0 and len(matches) > 1:
        # Ambiguous snippets need an explicit nonzero occurrence index,
        # or a longer uniquely identifying quote.
        raise EvidenceAnchorError(f"{where}: quote is repeated; use an unambiguous longer excerpt")


def verify_evidence_anchors(report: Mapping[str, Any], source: bytes, translation: bytes) -> None:
    """Reject fabricated/stale quotes before durable review evidence is written."""
    if report.get("evidence_version") != 2:
        return
    try:
        original = source.decode("utf-8")
        target = translation.decode("utf-8")
    except UnicodeError as exc:
        raise EvidenceAnchorError("source and translation must be valid UTF-8 for anchored review") from exc
    for i, finding in enumerate(report["findings"]):
        _check_anchor(finding["source_anchor"], original, f"findings[{i}].source_anchor")
        if finding["translation_anchor"] is not None:
            _check_anchor(finding["translation_anchor"], target, f"findings[{i}].translation_anchor")


def verify_correction_lineage(
    report: Mapping[str, Any] | None,
    *,
    previous: Mapping[str, Any] | None,
    source_sha256: str,
    translation_sha256: str,
    outcome: str,
) -> None:
    """Do not allow a previous anchored unresolved issue to disappear on re-review."""
    if previous is None or previous.get("source_sha256") != source_sha256:
        return
    prior_report = previous.get("quality_report")
    if (not isinstance(prior_report, Mapping)
            or prior_report.get("evidence_version") != 2
            or previous.get("outcome") != "CORRECTIONS_REQUIRED"):
        return
    unresolved = {
        finding["id"]: finding for finding in prior_report["findings"]
        if not finding["resolved"]
    }
    if not unresolved:
        return
    if not isinstance(report, Mapping) or report.get("evidence_version") != 2:
        raise EvidenceAnchorError("follow-up to anchored corrections requires v2 evidence")
    references: dict[str, Mapping[str, Any]] = {}
    for current in report["findings"]:
        ref = current["resolves"]
        if ref is None or ref["record_id"] != previous["record_id"]:
            continue
        old_id = ref["finding_id"]
        if old_id not in unresolved:
            raise EvidenceAnchorError(f"unknown prior unresolved finding: {old_id}")
        if old_id in references:
            raise EvidenceAnchorError(f"prior finding {old_id} was rechecked twice")
        references[old_id] = current
        if current["resolved"] and translation_sha256 == previous["translation_sha256"]:
            raise EvidenceAnchorError(
                "cannot mark a prior correction resolved without a changed translation artifact"
            )
    missing = sorted(set(unresolved) - set(references))
    if missing:
        raise EvidenceAnchorError("prior unresolved findings lack explicit rechecks: " + ", ".join(missing))
    if outcome == "PASS" and any(not finding["resolved"] for finding in references.values()):
        raise EvidenceAnchorError("PASS requires all previously unresolved corrections to be resolved")
