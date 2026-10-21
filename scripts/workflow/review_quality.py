"""Compact, validated literary review evidence tied to one accepted artifact."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .review_anchors import EvidenceAnchorError, validate_v2_finding


REQUIRED_CHECKS = ("coverage", "meaning", "terminology", "style", "structure")
CONTEXTS = {"independent_session", "bounded_context"}
SEVERITIES = {"minor", "major", "critical"}


class ReviewQualityError(ValueError):
    """A review report is incomplete or contradicts its outcome."""


def validate_quality_report(report: Any, *, outcome: str) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise ReviewQualityError("quality report must be an object")
    anchored = report.get("evidence_version") == 2
    required = {"context", "checks", "findings"}
    if anchored:
        required.add("evidence_version")
    if set(report) != required:
        raise ReviewQualityError("quality report needs exactly context, checks, findings and optional evidence_version=2")
    context = report["context"]
    if not isinstance(context, str) or context not in CONTEXTS:
        raise ReviewQualityError("context must be independent_session or bounded_context")
    checks = report["checks"]
    if not isinstance(checks, Mapping) or set(checks) != set(REQUIRED_CHECKS):
        raise ReviewQualityError("checks must cover coverage, meaning, terminology, style and structure")
    if any(type(checks[key]) is not bool for key in REQUIRED_CHECKS):
        raise ReviewQualityError("all checks must be boolean")
    if not all(checks[key] for key in REQUIRED_CHECKS):
        raise ReviewQualityError("every literary check must have been performed")
    findings = report["findings"]
    if not isinstance(findings, list) or len(findings) > 100:
        raise ReviewQualityError("findings must be a list with at most 100 entries")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, finding in enumerate(findings):
        if anchored:
            try:
                current = validate_v2_finding(finding, i)
            except EvidenceAnchorError as exc:
                raise ReviewQualityError(str(exc)) from exc
            if current["id"] in seen:
                raise ReviewQualityError(f"duplicate anchored finding id: {current['id']}")
            seen.add(current["id"])
            normalized.append(current)
        else:
            if not isinstance(finding, Mapping) or set(finding) != {"severity", "detail", "resolved"}:
                raise ReviewQualityError(f"finding {i} needs severity, detail and resolved")
            if not isinstance(finding["severity"], str) or finding["severity"] not in SEVERITIES:
                raise ReviewQualityError(f"finding {i} severity is invalid")
            detail = finding["detail"]
            if not isinstance(detail, str) or not 1 <= len(detail.strip()) <= 1000:
                raise ReviewQualityError(f"finding {i} needs a concise description")
            if type(finding["resolved"]) is not bool:
                raise ReviewQualityError(f"finding {i} resolved must be boolean")
            normalized.append(dict(finding))
    open_findings = [f for f in normalized if not f["resolved"]]
    if outcome == "PASS" and any(f["severity"] in {"major", "critical"} for f in open_findings):
        raise ReviewQualityError("PASS is incompatible with unresolved major/critical findings")
    if outcome == "CORRECTIONS_REQUIRED" and not open_findings:
        raise ReviewQualityError("CORRECTIONS_REQUIRED needs at least one unresolved finding")
    result = {"context": context, "checks": dict(checks), "findings": normalized}
    if anchored:
        result = {"evidence_version": 2, **result}
    return result
