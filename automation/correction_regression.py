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


def source_aligned_supported(engine) -> bool:
    """Only an explicitly opted-in pinned workflow may use this guard."""
    return (engine / "docs/SOURCE_ALIGNED_REPETITION.md").is_file()


def require_source_supported_repetitions(source: str, before: str, candidate: str) -> None:
    """Permit only source-supported cross-block repeats to reach literary review.

    Exact source-owned block alignment is mandatory. Every new within-block
    repeat remains blocked. Across blocks, identical candidate phrases may
    recur once per block only if all corresponding source blocks have a
    common exact five-word phrase. This is a structural eligibility check,
    not cross-language semantic proof: independent preflight AND full literary
    review remain mandatory. No raw source or target phrases enter evidence.
    """
    from review_chunks import BLOCK_MARKER

    def split(text):
        matches = list(BLOCK_MARKER.finditer(text))
        return ([m.group(1) for m in matches],
                text[:matches[0].start()] if matches else text,
                [text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
                 for i, m in enumerate(matches)])

    si, sp, sb = split(source)
    bi, bp, bb = split(before)
    ci, cp, cb = split(candidate)
    if (not 2 <= len(si) <= 128 or si != bi or si != ci or len(si) != len(set(si))
            or bp != cp or max(len(source), len(before), len(candidate)) > 250_000):
        require_no_new_repetitions(before, candidate)
        return
    # Protected-marker sequence is independently verified by the runner.
    # Do not count marker identifiers as words, or ngrams crossing blocks.
    for old, new in zip(bb, cb):
        require_no_new_repetitions(old, new)
    source_words = [_tokens(block) for block in sb]
    old_words = [_tokens(block) for block in bb]
    new_words = [_tokens(block) for block in cb]
    source_windows = [set(_windows(words, 5)) for words in source_words]
    for width in range(5, 13):
        old_counts = Counter()
        new_counts = Counter()
        locations = {}
        for i, (old, new) in enumerate(zip(old_words, new_words)):
            old_counts.update(_windows(old, width))
            counts = _windows(new, width)
            new_counts.update(counts)
            for phrase, count in counts.items():
                locations.setdefault(phrase, []).append((i, count))
        for phrase, count in new_counts.items():
            if count < 2 or count <= old_counts[phrase]:
                continue
            blocks = locations[phrase]
            common = set(source_windows[blocks[0][0]])
            for i, _ in blocks[1:]:
                common.intersection_update(source_windows[i])
            if len(blocks) < 2 or any(n != 1 for _, n in blocks) or not common:
                raise CorrectionRegression("new-duplicated-phrase", {
                    "count": 1, "examples": [{"tokens": width,
                        "previous": old_counts[phrase], "candidate": count,
                        "adjacent": False}]})


def require_candidate_repetitions(source: str, before: str, candidate: str, *,
                                  source_aligned: bool = False) -> None:
    if source_aligned:
        require_source_supported_repetitions(source, before, candidate)
    else:
        require_no_new_repetitions(before, candidate)


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
