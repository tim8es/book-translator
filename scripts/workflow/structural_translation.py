"""Pure shared link/EPUB-marker translation planning and deterministic assembly.

This helper is not a literary workflow revision. Runtime adapters may pin its
public Git SHA independently from the book's canonical literary workflow.
Models translate ordered text segments. The original source owns all technical
links, URLs, images and EPUB markers; no model-generated address is trusted.
"""
from __future__ import annotations

import hashlib
import json
import re

LINK_RE = re.compile(
    r"(?P<image>!?)\[(?P<label>[^\]\n]*)\]\("
    r"(?P<destination><[^>\n]+>|[^)\s]+)(?P<title>\s+[^)\n]*)?\)"
)
EPUB_MARKER_RE = re.compile(r"<!--\s*bt-(?:anchor|block):[^>]*-->")
RESERVED_TOKEN_RE = re.compile(r"ZXBTLINK[0-9a-f]{10}N\d{4}(?:START|END)XZ")
ID_RE = re.compile(r"^s[0-9]{5}$")
SCHEMA_VERSION = 1
MAX_SEGMENTS_PER_MODEL_CALL = 3


class StructureError(ValueError):
    """Structural translation cannot be assembled safely."""


def _protected_matches(source: str) -> list[tuple[int, int, str, str]]:
    result = []
    for kind, pattern in (("link", LINK_RE), ("epub_marker", EPUB_MARKER_RE)):
        for match in pattern.finditer(source):
            result.append((match.start(), match.end(), kind, match.group(0)))
    result.sort(key=lambda item: (item[0], item[1]))
    if any(left[0] < right[1] and right[0] < left[1]
           for left, right in zip(result, result[1:])):
        raise StructureError("overlapping protected source constructs")
    return result


def plan(source: str) -> dict | None:
    """Build source-bound ordered text and immutable-layout spans, or None."""
    if not isinstance(source, str) or not source.strip():
        raise StructureError("source unit must contain text")
    if RESERVED_TOKEN_RE.search(source):
        raise StructureError("source contains reserved boundary-token syntax")
    protected = _protected_matches(source)
    if not protected:
        return None
    parts = []
    next_id = 0

    def add_text(raw: str) -> None:
        nonlocal next_id
        if not raw:
            return
        if not raw.strip():
            parts.append({"kind": "literal", "value": raw})
            return
        prefix = re.match(r"\s*", raw).group(0)
        suffix = re.search(r"\s*$", raw).group(0)
        core = raw[len(prefix):len(raw)-len(suffix) if suffix else len(raw)]
        next_id += 1
        if next_id > 99999:
            raise StructureError("too many translatable source spans")
        parts.append({"kind": "text", "id": f"s{next_id:05d}",
                      "original": core, "before": prefix, "after": suffix})

    cursor = 0
    for start, end, kind, raw in protected:
        add_text(source[cursor:start])
        parts.append({"kind": kind, "value": raw})
        cursor = end
    add_text(source[cursor:])
    return {"schema_version": SCHEMA_VERSION,
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "parts": parts}


def _validate_plan(value: object) -> None:
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "source_sha256", "parts"}
            or value["schema_version"] != SCHEMA_VERSION
            or not isinstance(value["parts"], list)
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["source_sha256"]))):
        raise StructureError("invalid translation structure plan")
    ids = []
    for part in value["parts"]:
        if not isinstance(part, dict) or not isinstance(part.get("kind"), str):
            raise StructureError("invalid translation part")
        kind = part["kind"]
        if kind == "text":
            if (set(part) != {"kind", "id", "original", "before", "after"}
                    or not ID_RE.fullmatch(str(part["id"]))
                    or any(not isinstance(part[field], str)
                           for field in ("original", "before", "after"))):
                raise StructureError("invalid text segment")
            ids.append(part["id"])
        elif kind in {"literal", "link", "epub_marker"}:
            if set(part) != {"kind", "value"} or not isinstance(part["value"], str):
                raise StructureError("invalid protected part")
        else:
            raise StructureError("unknown translation part kind")
    if len(ids) != len(set(ids)):
        raise StructureError("duplicate text segment IDs")


def spans(translation_plan: dict) -> list[dict]:
    _validate_plan(translation_plan)
    return [{"id": item["id"], "source": item["original"]}
            for item in translation_plan["parts"] if item["kind"] == "text"]


def next_spans(translation_plan: dict, completed: dict,
               limit: int = MAX_SEGMENTS_PER_MODEL_CALL) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= MAX_SEGMENTS_PER_MODEL_CALL:
        raise StructureError("invalid structural model batch size")
    available = spans(translation_plan)
    ids = {x["id"] for x in available}
    if (not isinstance(completed, dict)
            or any(not isinstance(k, str) or k not in ids
                   or not isinstance(v, str) or not v.strip()
                   for k, v in completed.items())):
        raise StructureError("invalid persisted segment translations")
    return [x for x in available if x["id"] not in completed][:limit]



# Only rejected model-output protocol defects are eligible for focused retry.
# Source, persisted-state and plan validation errors are never model retries.
_PROTOCOL_FAILURES = {
    "incomplete structural translation JSON": "json_incomplete",
    "structural translator must return JSON": "json_invalid",
    "structural translator JSON schema mismatch": "schema_mismatch",
    "structural translation omitted or overflowed a batch": "invalid_batch_size",
    "unknown or duplicate translation segment": "invalid_segment_id",
    "unsafe translated segment": "unsafe_segment",
}


def protocol_failure_code(error: str) -> str | None:
    """Classify rejected model output, never an infrastructure failure."""
    return _PROTOCOL_FAILURES.get(error) if isinstance(error, str) else None


def repair_guidance(selected: list[dict], error: str) -> str:
    """Explain one source-ID-bound retry without trusting model coordinates."""
    failure = protocol_failure_code(error)
    if failure is None:
        raise StructureError("structural failure is not a model protocol error")
    if (not isinstance(selected, list) or len(selected) != 1
            or not isinstance(selected[0], dict)
            or not ID_RE.fullmatch(str(selected[0].get("id", "")))
            or not isinstance(selected[0].get("source"), str)
            or not selected[0]["source"].strip()):
        raise StructureError("focused structural retry requires one source span")
    return json.dumps({
        "previous_response_rejected": failure,
        "required_segment_id": selected[0]["id"],
        "instructions": (
            "Translate only the supplied single source span. Return a JSON object, "
            "not Markdown or commentary. Use exactly the required ID; no extra IDs, "
            "URLs, links, EPUB markers or placeholders. Preserve the complete "
            "meaning and paragraph structure of the source."
        ),
        "required_shape": {
            "segments": [{"id": selected[0]["id"], "translation": "translated source text"}],
            "glossary": [], "style_guide": [],
        },
    }, ensure_ascii=False)


def coverage_alert(source: str, translation: str) -> bool:
    """Conservative pre-acceptance truncation alarm; not a literary PASS."""
    if not isinstance(source, str) or not isinstance(translation, str):
        raise StructureError("coverage input must be text")
    source_paragraphs = sum(bool(p.strip()) for p in re.split(r"\n\s*\n", source))
    translation_paragraphs = sum(bool(p.strip()) for p in re.split(r"\n\s*\n", translation))
    return (len(source) >= 3500 and source_paragraphs >= 15
            and len(translation) * 100 < len(source) * 70
            and translation_paragraphs * 100 < source_paragraphs * 70)

def parse_batch(response: str, expected_ids: list[str]) -> tuple[dict, list, list]:
    """Accept only nonempty, source-identified translation proposals."""
    if (not isinstance(response, str) or not isinstance(expected_ids, list)
            or not expected_ids or len(set(expected_ids)) != len(expected_ids)
            or any(not isinstance(x, str) or not ID_RE.fullmatch(x)
                   for x in expected_ids)):
        raise StructureError("invalid structural translation batch")
    value = response.strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        lines = value.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith(fence):
            raise StructureError("incomplete structural translation JSON")
        value = "\n".join(lines[1:-1])
    try:
        data = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise StructureError("structural translator must return JSON") from exc
    if not isinstance(data, dict) or set(data) != {
        "segments", "glossary", "style_guide"
    }:
        raise StructureError("structural translator JSON schema mismatch")
    raw = data["segments"]
    if (not isinstance(raw, list) or not raw or len(raw) > len(expected_ids)
            or not isinstance(data["glossary"], list)
            or not isinstance(data["style_guide"], list)):
        raise StructureError("structural translation omitted or overflowed a batch")
    values = {}
    for item in raw:
        if (not isinstance(item, dict) or set(item) != {"id", "translation"}
                or not isinstance(item["id"], str)
                or item["id"] not in expected_ids or item["id"] in values):
            raise StructureError("unknown or duplicate translation segment")
        translated = item["translation"]
        if (not isinstance(translated, str) or not translated.strip()
                or RESERVED_TOKEN_RE.search(translated)
                or EPUB_MARKER_RE.search(translated)
                or LINK_RE.search(translated)):
            raise StructureError("unsafe translated segment")
        values[item["id"]] = translated
    return values, data["glossary"], data["style_guide"]


def render(translation_plan: dict, completed: dict) -> str:
    """Never ask a language model to reproduce a protected URL or marker."""
    _validate_plan(translation_plan)
    required = {p["id"] for p in translation_plan["parts"] if p["kind"] == "text"}
    if not isinstance(completed, dict) or set(completed) != required:
        raise StructureError("missing or extra translated segments")
    result = []
    for item in translation_plan["parts"]:
        if item["kind"] == "text":
            translated = completed[item["id"]]
            if (not isinstance(translated, str) or not translated.strip()
                    or RESERVED_TOKEN_RE.search(translated)
                    or EPUB_MARKER_RE.search(translated)
                    or LINK_RE.search(translated)):
                raise StructureError("unsafe translated text fragment")
            result.extend((item["before"], translated.strip(), item["after"]))
        else:
            result.append(item["value"])
    assembled = "".join(result)
    expected = [p["value"] for p in translation_plan["parts"]
                if p["kind"] in {"link", "epub_marker"}]
    actual = [match[3] for match in _protected_matches(assembled)]
    if actual != expected:
        raise StructureError("protected link or marker changed during assembly")
    return assembled.rstrip() + "\n"
