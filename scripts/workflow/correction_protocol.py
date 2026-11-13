"""Canonical, side-effect-free protocol for source-bound literary corrections.

This protocol does not accept or promote translations. The Orchestrator keeps
ownership of claims, SHA-bound reviewer evidence, text edits and independent
Reviewer PASS. Automated providers must not define their own patch semantics.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

MAX_REPLACEMENT_CHARS = 10_000
MAX_CORRECTION_FINDINGS = 30
_REPAIRABLE = {
    "corrector patch has invalid fields": "invalid_fields",
    "corrector returned no actionable patches": "missing_patches",
    "corrector replacement is empty or too large": "invalid_replacement",
    "corrector replacement must be a string": "invalid_replacement",
    "corrector replacement exceeds maximum length": "invalid_replacement",
    "corrector insertion requires nonempty replacement": "empty_insertion",
    "insertion_anchor is only allowed for omissions": "wrong_anchor",
    "an omission requires an insertion patch": "wrong_operation",
    "corrector patch operation is unsupported": "wrong_operation",
    "corrector insertion anchor is invalid": "invalid_anchor",
    "corrector response must contain patches, glossary, style_guide": "invalid_fields",
    "corrector must return a structured patch response": "invalid_fields",
    "corrector patch must identify one unresolved finding exactly once": "invalid_fields",
    "corrector patches are not bounded or no findings are open": "invalid_fields",
    "patch must target the reviewer's exact translation anchor": "wrong_anchor",
}


class CorrectionProtocolError(ValueError):
    """Model-generated patch is invalid; never alter current accepted text."""

    def __init__(self, code: str, finding_id: str | None = None):
        self.code = code
        self.finding_id = finding_id
        super().__init__(code)


def failure_code(error: str) -> str | None:
    """Identify repairable *provider output* failures, not transport/integrity.

    Exact messages are intentional. New errors require an explicit decision
    before automation can classify them as safe to retry.
    """
    if not isinstance(error, str):
        return None
    return _REPAIRABLE.get(error)


def repair_spec(findings: Sequence[Mapping], error: str | None = None) -> dict:
    """Type operations from reviewer-owned evidence, never a model's category.

    In particular, 'omission' with a non-null translation_anchor is NOT a
    free-form insertion. Exact coordinates are reviewer-owned in this case.
    """
    if (not isinstance(findings, (list, tuple))
            or not 1 <= len(findings) <= MAX_CORRECTION_FINDINGS):
        raise CorrectionProtocolError("invalid_finding_batch")
    seen = set()
    items = []
    for finding in findings:
        if (not isinstance(finding, Mapping) or not isinstance(finding.get("id"), str)
                or not finding["id"] or finding["id"] in seen
                or finding.get("resolved") is not False):
            raise CorrectionProtocolError("invalid_reviewer_finding")
        seen.add(finding["id"])
        anchor = finding.get("translation_anchor")
        if anchor is not None and (not isinstance(anchor, Mapping)
                                   or not isinstance(anchor.get("quote"), str)
                                   or not isinstance(anchor.get("occurrence"), int)
                                   or isinstance(anchor.get("occurrence"), bool)):
            raise CorrectionProtocolError("invalid_reviewer_anchor", finding["id"])
        omission = anchor is None
        items.append({
            "finding_id": finding["id"],
            "operation": ("insert_before|insert_after" if omission
                          else "replace|insert_before|insert_after"),
            "required_keys": (
                ["finding_id", "operation", "replacement", "insertion_anchor"]
                if omission else ["finding_id", "operation", "replacement"]
            ),
            "reviewer_bound": not omission,
            "instruction": (
                "Translation anchor is null. Supply a UNIQUE nearby translation "
                "quote as insertion_anchor:{quote,occurrence}; write ONLY omitted "
                "source content, not an entire chapter or paragraph rewrite."
                if omission else
                "The reviewer already selected exact translation coordinates. "
                "Never supply insertion_anchor or target_anchor, even when the "
                "finding category is omission. Keep replacements local."
            ),
        })
    return {
        "schema_version": 1,
        "output_exact_keys": ["patches", "glossary", "style_guide"],
        "max_replacement_characters": MAX_REPLACEMENT_CHARS,
        "items": items,
        "previous_response_rejected": failure_code(error) if error else None,
        "safety": "No source mutation, no change outside reviewed anchors, no PASS by corrector.",
    }


def repair_guidance(findings: Sequence[Mapping], previous_error: str) -> str:
    """Machine-readable corrective instruction for a single bounded retry."""
    code = failure_code(previous_error)
    if code is None:
        raise CorrectionProtocolError("unclassified_failure")
    data = repair_spec(findings, previous_error)
    data["retry_instruction"] = (
        "Your previous output was rejected before any edit was applied. "
        "Return only valid JSON matching the fields above. Every proposed "
        "nonempty insertion must be shorter than max_replacement_characters. "
        "Do not summarize or replace the whole chapter. An empty replacement "
        "is valid only for an explicit removal justified by a reviewed addition. "
        "Keep reviewer-bound coordinates unchanged; do not invent a new target."
    )
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def validate_replacement(patch: Mapping, finding: Mapping,
                         max_chars: int = MAX_REPLACEMENT_CHARS) -> str:
    """Classify replacement failures separately without weakening limits."""
    value = patch.get("replacement")
    if not isinstance(value, str):
        raise CorrectionProtocolError("not_a_string", str(finding.get("id", "")))
    if len(value) > max_chars:
        raise CorrectionProtocolError("too_large", str(finding.get("id", "")))
    if not value.strip():
        # A valid reviewed addition can justify deletion. A source omission
        # cannot be 'corrected' by inserting whitespace or empty text.
        if (finding.get("translation_anchor") is None
                or patch.get("operation") != "replace"
                or finding.get("category") != "addition"):
            raise CorrectionProtocolError("empty", str(finding.get("id", "")))
    return value


def validate_intent(patch: Mapping, finding: Mapping) -> None:
    """Reject an address/operation inconsistent with immutable reviewer data."""
    if not isinstance(patch, Mapping) or not isinstance(finding, Mapping):
        raise CorrectionProtocolError("invalid_patch")
    if patch.get("finding_id") != finding.get("id"):
        raise CorrectionProtocolError("unbound_finding")
    anchor = finding.get("translation_anchor")
    operation = patch.get("operation")
    if anchor is None:
        if operation not in {"insert_before", "insert_after"}:
            raise CorrectionProtocolError("missing_insertion")
        candidate = patch.get("insertion_anchor", patch.get("target_anchor"))
        if (not isinstance(candidate, Mapping)
                or not isinstance(candidate.get("quote"), str)
                or not candidate["quote"].strip()
                or type(candidate.get("occurrence")) is not int):
            raise CorrectionProtocolError("invalid_insertion_anchor")
    else:
        if operation not in {"replace", "insert_before", "insert_after"}:
            raise CorrectionProtocolError("unsupported_bound_operation")
        for name in ("insertion_anchor", "target_anchor"):
            if name in patch and patch[name] != anchor:
                raise CorrectionProtocolError("model_changed_reviewer_anchor")
    validate_replacement(patch, finding)
