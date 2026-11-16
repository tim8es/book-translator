"""Conservative, source-neutral pre-acceptance guards for correction candidates.

This module never grants PASS or resolves reviewer findings.  It can only
reject an obviously suspicious candidate before the canonical text is changed.
All reports contain hashes/counts, not excerpts from protected books.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re

WORDS = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)


class CorrectionRegression(ValueError):
    def __init__(self, code: str, evidence: dict):
        self.code = code
        self.evidence = evidence
        super().__init__(code)


def _tokens(text: str) -> list[str]:
    return [match.group().casefold() for match in WORDS.finditer(text)]


def _windows(tokens: list[str], width: int) -> Counter:
    return Counter(tuple(tokens[i:i + width])
                   for i in range(len(tokens) - width + 1))


def _adjacent_repeats(tokens: list[str]) -> Counter:
    """Local exact echoes of 2-4 words, excluding ordinary single-word emphasis."""
    result: Counter = Counter()
    for width in (2, 3, 4):
        for pos in range(len(tokens) - 2 * width + 1):
            group = tuple(tokens[pos:pos + width])
            if group == tuple(tokens[pos + width:pos + 2 * width]):
                result[group] += 1
    return result


def new_repetitions(before: str, candidate: str) -> list[dict]:
    """Report only repetitions *added* by the patch, never pre-existing repeats.

    Five-or-more-token phrases repeated anywhere, or immediate echoes of two
    to four tokens, trigger a conservative review hold. The guard deliberately
    does not rewrite prose or declare a literary judgment.
    """
    old, new = _tokens(before), _tokens(candidate)
    if old == new:
        return []
    issues = {}
    old_near, new_near = _adjacent_repeats(old), _adjacent_repeats(new)
    for phrase, count in new_near.items():
        prior = old_near[phrase]
        if count > prior:
            issues[phrase] = (prior, count, True)
    for width in range(5, 13):
        old_windows, new_windows = _windows(old, width), _windows(new, width)
        for phrase, count in new_windows.items():
            prior = old_windows[phrase]
            if count >= 2 and count > prior:
                issues[phrase] = (prior, count, False)
    # Prefer longest examples of the same regression; never print book text.
    result = []
    for phrase in sorted(issues, key=lambda p: (-len(p), p)):
        if any(len(phrase) < len(existing) and
               any(existing[i:i + len(phrase)] == phrase
                   for i in range(len(existing) - len(phrase) + 1))
               for existing in (r["_phrase"] for r in result)):
            continue
        prior, count, adjacent = issues[phrase]
        result.append({
            "_phrase": phrase,
            "tokens": len(phrase),
            "previous": prior,
            "candidate": count,
            "adjacent": adjacent,
            "fingerprint": hashlib.sha256(
                " ".join(phrase).encode("utf-8")).hexdigest(),
        })
        if len(result) >= 8:
            break
    for item in result:
        item.pop("_phrase")
    return result


def require_no_new_repetitions(before: str, candidate: str) -> None:
    evidence = new_repetitions(before, candidate)
    if evidence:
        raise CorrectionRegression("new-duplicated-phrase", {
            "count": len(evidence), "examples": evidence[:4]})


def patch_fingerprint(patch: dict) -> str:
    return hashlib.sha256(json.dumps(
        patch, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def classify_finding(finding: dict) -> str:
    """Diagnostic triage only; the pinned reviewer contract stays authoritative.

    No category here automatically waives a finding or authorizes PASS.
    """
    severity = finding.get("severity")
    category = finding.get("category")
    if severity in {"major", "critical"} or category in {
            "omission", "addition", "meaning", "modality", "referent",
            "structure", "voice", "terminology"}:
        return "required"
    if severity == "minor" and category in {"style", "register"}:
        return "adjudicate"
    return "review_required"


def triage_counts(findings: list[dict]) -> dict:
    result = {"required": 0, "adjudicate": 0, "review_required": 0}
    for finding in findings:
        result[classify_finding(finding)] += 1
    return result
