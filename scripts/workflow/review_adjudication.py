"""Canonical evidence for independently adjudicated editorial disagreements."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from .review_anchors import EvidenceAnchorError, SHA256

ELIGIBLE = frozenset({"style", "register", "voice"})
FIELDS = {"finding_id", "decision", "reason", "source_sha256",
          "translation_sha256", "reviewer_session_id", "adjudicator_session_id"}


def eligible(finding):
    return (isinstance(finding, Mapping) and finding.get("severity") == "minor"
            and finding.get("category") in ELIGIBLE
            and finding.get("translation_anchor") is not None)


def validate(entries, findings):
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise EvidenceAnchorError("editorial adjudications must be a nonempty bounded list")
    by_id = {f["id"]: f for f in findings}
    seen = set()
    result = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != FIELDS:
            raise EvidenceAnchorError("invalid editorial adjudication schema")
        fid = entry["finding_id"]
        if not isinstance(fid, str) or fid in seen or fid not in by_id:
            raise EvidenceAnchorError("editorial adjudication finding is absent or duplicate")
        finding = by_id[fid]
        if not eligible(finding) or finding["resolved"] is not True:
            raise EvidenceAnchorError("only a closed minor style/register/voice dispute is eligible")
        if entry["decision"] != "acceptable_variant":
            raise EvidenceAnchorError("unsupported editorial adjudication decision")
        if not isinstance(entry["reason"], str) or not 20 <= len(entry["reason"].strip()) <= 1000:
            raise EvidenceAnchorError("editorial adjudication needs a concrete rationale")
        for key in ("source_sha256", "translation_sha256"):
            if not isinstance(entry[key], str) or not SHA256.fullmatch(entry[key]):
                raise EvidenceAnchorError("invalid editorial adjudication artifact hash")
        for key in ("reviewer_session_id", "adjudicator_session_id"):
            if not isinstance(entry[key], str) or not 1 <= len(entry[key]) <= 200:
                raise EvidenceAnchorError("invalid editorial adjudication session")
        if entry["reviewer_session_id"] == entry["adjudicator_session_id"]:
            raise EvidenceAnchorError("editorial adjudication must be independent")
        seen.add(fid)
        result.append(dict(entry))
    return result


def verify_artifacts(report, source, translation):
    if "adjudications" not in report:
        return
    for entry in validate(report["adjudications"], report["findings"]):
        if (entry["source_sha256"] != hashlib.sha256(source).hexdigest()
                or entry["translation_sha256"] != hashlib.sha256(translation).hexdigest()):
            raise EvidenceAnchorError("editorial adjudication is stale")


def verify_sessions(report, reviewer_session_id, translator_session_id):
    for entry in report.get("adjudications", []):
        if (entry["reviewer_session_id"] != reviewer_session_id
                or entry["adjudicator_session_id"] == translator_session_id
                or report["context"] != "independent_session"):
            raise EvidenceAnchorError("editorial adjudication session does not match independent review")


def permits_unchanged(report, current, prior, source_sha256, translation_sha256):
    if (not eligible(prior) or not eligible(current)
            or current["category"] != prior["category"]
            or current["source_anchor"] != prior["source_anchor"]
            or current["translation_anchor"] != prior["translation_anchor"]):
        return False
    return any(e["finding_id"] == current["id"]
               and e["source_sha256"] == source_sha256
               and e["translation_sha256"] == translation_sha256
               for e in report.get("adjudications", []))
