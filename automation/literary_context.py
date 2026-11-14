"""Manifest-routed literary prompts and claim-bound shared-memory reconciliation.

This is an adapter for the *pinned* canonical workflow, not a second state engine.
Translator proposes glossary entries; independent Reviewer approves style observations.
The Orchestrator persists one target per claim through canonical ProposalManager.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from omniroute import ModelError, parse_review

MAX_SHARED_CHARS = 100_000
MAX_ITEMS = 40
# shortcut: patches cap at 30 per response; raise only for a measured unit needing more.
MAX_PATCHES = 30
MAX_PATCH_CHARS = 10_000
_ALLOWED_ROLES = {"translator", "reviewer"}
_NAME_KEYS = {"original", "translation", "type", "notes"}
_STYLE_KEYS = {"source_pattern", "translation_approach", "evidence"}
# Corrector supplies an edit intent; known target coordinates come ONLY from the
# reviewer's hash-bound v2 evidence. Legacy targets must match it exactly.
_PATCH_KEYS = {"finding_id", "operation", "replacement"}
_OMISSION_PATCH_KEYS = _PATCH_KEYS | {"insertion_anchor"}
_LEGACY_PATCH_KEYS = _PATCH_KEYS | {"target_anchor"}
_FINDING_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class PatchAnchorMismatch(ModelError):
    """An actionable, bounded mismatch; never contains model replacement text."""

    def __init__(self, finding_id: str, expected: dict, received: dict):
        super().__init__("patch must target the reviewer's exact translation anchor")
        self.finding_id = finding_id
        self.expected_anchor = dict(expected)
        self.received_anchor = dict(received)


def _one_line(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 700:
        raise ModelError(f"invalid literary memory field: {field}")
    if "\n" in value or "\r" in value or "<!--" in value:
        raise ModelError(f"unsafe literary memory field: {field}")
    return value.strip()


def _entries(raw: object, keys: set[str], field: str) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise ModelError(f"{field} must be a bounded list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != keys:
            raise ModelError(f"{field} entry has invalid keys")
        result.append({k: _one_line(entry[k], f"{field}.{k}") for k in sorted(keys)})
    return result


def _json_payload(text: str) -> object:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith("```"):
            raise ModelError("incomplete fenced model response")
        value = "\n".join(lines[1:-1])
    return json.loads(value)


def parse_translation(text: str) -> tuple[str, list[dict], list[dict]]:
    try:
        data = _json_payload(text)
    except ValueError as exc:
        raise ModelError("translator must return a structured JSON response") from exc
    if not isinstance(data, dict) or set(data) != {"translation", "glossary", "style_guide"}:
        raise ModelError("translator response must contain translation, glossary, style_guide")
    translation = data["translation"]
    if not isinstance(translation, str) or not translation.strip():
        raise ModelError("empty chapter translation")
    return (translation.rstrip() + "\n",
            _entries(data["glossary"], _NAME_KEYS, "glossary"),
            _entries(data["style_guide"], _STYLE_KEYS, "style_guide"))


def parse_literary_review(
    text: str, *, context: str = "independent_session",
) -> tuple[str, dict, list[dict]]:
    try:
        data = _json_payload(text)
    except ValueError as exc:
        raise ModelError("reviewer must return JSON") from exc
    if not isinstance(data, dict) or data.get("outcome") not in {"PASS", "CORRECTIONS_REQUIRED"}:
        raise ModelError("reviewer response has invalid literary memory schema")
    if set(data) == {"outcome", "checks", "findings", "style_guide"}:
        core = {key: data[key] for key in ("outcome", "checks", "findings")}
        outcome, report = parse_review(json.dumps(core, ensure_ascii=False))
    elif set(data) == {"evidence_version", "outcome", "checks", "findings", "style_guide"}:
        if type(data["evidence_version"]) is not int or data["evidence_version"] != 2:
            raise ModelError("unsupported reviewer evidence version")
        if context not in {"independent_session", "bounded_context"}:
            raise ModelError("reviewer context is invalid")
        try:
            from workflow.review_quality import validate_quality_report
            report = validate_quality_report({
                "evidence_version": 2,
                "context": context,
                "checks": data["checks"],
                "findings": data["findings"],
            }, outcome=data["outcome"])
        except (ImportError, ValueError) as exc:
            raise ModelError(f"reviewer evidence is invalid: {exc}") from exc
        outcome = data["outcome"]
    else:
        raise ModelError("reviewer response has invalid literary memory schema")
    style = _entries(data["style_guide"], _STYLE_KEYS, "style_guide")
    return outcome, report, style


def _insertion_anchor(value: object) -> dict:
    """Validate a model-selected insertion location for a true omission only."""
    if (not isinstance(value, dict) or set(value) != {"quote", "occurrence"}
            or not isinstance(value["quote"], str)
            or not 1 <= len(value["quote"]) <= 300
            or not value["quote"].strip()
            or type(value["occurrence"]) is not int
            or not 0 <= value["occurrence"] < 10000):
        raise ModelError("corrector insertion anchor is invalid")
    return dict(value)


def parse_correction_patches(
    text: str, findings: list[dict], *, allow_partial: bool = False,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Bind each edit to immutable reviewer evidence, never model-made coordinates.

    Known reviewer anchors are authoritative. A legacy target_anchor is allowed
    only when IDENTICAL to the reviewer quote: silently rebinding a larger model
    patch to a smaller reviewer quote could duplicate a paragraph. Omissions
    require a validated, unique insertion_anchor in the current translation.
    The caller checks SHA-bound review identity and applies all edits atomically.
    """
    try:
        data = _json_payload(text)
    except ValueError as exc:
        raise ModelError("corrector must return a structured patch response") from exc
    if not isinstance(data, dict) or set(data) != {"patches", "glossary", "style_guide"}:
        raise ModelError("corrector response must contain patches, glossary, style_guide")
    unresolved = {item["id"]: item for item in findings if not item["resolved"]}
    patches = data["patches"]
    if not unresolved or not isinstance(patches, list) or len(patches) > MAX_PATCHES:
        raise ModelError("corrector patches are not bounded or no findings are open")
    result = []
    seen = set()
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) not in (
            _PATCH_KEYS, _OMISSION_PATCH_KEYS, _LEGACY_PATCH_KEYS
        ):
            raise ModelError("corrector patch has invalid fields")
        finding_id = patch["finding_id"]
        if (not isinstance(finding_id, str) or _FINDING_ID.fullmatch(finding_id) is None
                or finding_id not in unresolved or finding_id in seen):
            raise ModelError("corrector patch must identify one unresolved finding exactly once")
        seen.add(finding_id)
        operation = patch["operation"]
        if not isinstance(operation, str) or operation not in {"replace", "insert_before", "insert_after"}:
            raise ModelError("corrector patch operation is unsupported")
        reviewer_anchor = unresolved[finding_id]["translation_anchor"]
        if reviewer_anchor is None:
            if operation == "replace":
                raise ModelError("an omission requires an insertion patch")
            # No reviewed translation quote exists for omitted source material.
            # Require a model-proposed insertion location and validate its
            # uniqueness against the current text at the application boundary.
            choice = patch.get("insertion_anchor", patch.get("target_anchor"))
            anchor = _insertion_anchor(choice)
        else:
            if "insertion_anchor" in patch:
                # Redundant legacy addresses are safe ONLY if they exactly
                # repeat the immutable reviewer anchor. Ignore that redundant
                # field, never use model-selected coordinates for this finding.
                # A different location could change unrelated text: fail closed.
                if patch["insertion_anchor"] != reviewer_anchor:
                    raise PatchAnchorMismatch(
                        finding_id, reviewer_anchor, patch["insertion_anchor"])
            # The review record, not the model, owns the exact target address.
            # A legacy response may intend to rewrite its larger chosen quote;
            # silently applying that replacement to a shorter reviewed quote
            # could duplicate adjacent text. Reject mismatch without mutation.
            if ("target_anchor" in patch
                    and patch["target_anchor"] != reviewer_anchor):
                received = patch["target_anchor"]
                if not isinstance(received, dict):
                    raise ModelError("legacy corrector target_anchor is invalid")
                raise PatchAnchorMismatch(finding_id, reviewer_anchor, received)
            anchor = dict(reviewer_anchor)
        replacement = patch["replacement"]
        if not isinstance(replacement, str):
            raise ModelError("corrector replacement must be a string")
        if len(replacement) > MAX_PATCH_CHARS:
            raise ModelError("corrector replacement exceeds maximum length")
        if not replacement.strip() and operation != "replace":
            raise ModelError("corrector insertion requires nonempty replacement")
        result.append({"finding_id": finding_id, "operation": operation,
                       "target_anchor": anchor, "replacement": replacement})
    if not seen:
        raise ModelError("corrector returned no actionable patches")
    if not allow_partial and seen != set(unresolved):
        raise ModelError("corrector must address every unresolved review finding")
    return (result,
            _entries(data["glossary"], _NAME_KEYS, "glossary"),
            _entries(data["style_guide"], _STYLE_KEYS, "style_guide"))


def role_contracts(engine: Path, role: str) -> tuple[str, list[str]]:
    """Read selected revision's profile and validate every path before loading."""
    if role not in _ALLOWED_ROLES:
        raise ValueError("unsupported literary role")
    # The version-selected engine defines routing; this adapter must not
    # maintain a shadow copy of manifest path selection or optional overlays.
    scripts = str(engine / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from workflow.context_routing import contract_paths, read_manifest
    manifest = read_manifest(engine)
    files = contract_paths(manifest, role)
    mandatory = {"AGENTS.md", "docs/TRANSLATION.md",
                 "docs/TRANSLATOR.md" if role == "translator" else "docs/REVIEWER.md"}
    if not mandatory.issubset(set(files)):
        raise ValueError(f"missing required {role} role contracts")
    other_role = "docs/REVIEWER.md" if role == "translator" else "docs/TRANSLATOR.md"
    if other_role in files:
        raise ValueError(f"cross-role contract leakage in {role} profile")
    sections = []
    for path in files:
        target = (engine / path).resolve(strict=True)
        if not target.is_relative_to(engine.resolve()):
            raise ValueError("contract escapes pinned engine")
        contents = target.read_text("utf-8")
        if not contents.strip():
            raise ValueError(f"empty required {role} contract: {path}")
        sections.append(contents)
    return "\n\n".join(sections), files


def model_glossary_context(markdown: str, *, source_language: str,
                           target_language: str) -> str:
    """Explicit language-pair decisions for the model; no script assumptions.

    A decision is a target-language lexical form and usage note, not a mandatory
    literal substring. Parsing retains notes and handles escaped Markdown pipes.
    """
    entries = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip().replace(r"\|", "|")
                 for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 4 or not all(cells[:3]):
            continue
        original, translation, kind, notes = cells[:4]
        if original.casefold() in {"original", "source", "term", "оригинал"}:
            continue
        if re.fullmatch(r"[-:\s]+", original):
            continue
        entries.append({"original": original, "translation": translation,
                        "type": kind, "notes": notes})
    if not source_language or not target_language:
        raise ValueError("model glossary requires an explicit language pair")
    return json.dumps({
        "source_language": source_language, "target_language": target_language,
        "entries": entries,
        "unparsed_glossary": markdown if not entries else "",
        "interpretation": (
            "These are contextual lexical decisions, NOT strings to paste "
            "verbatim in every sentence. Apply grammar, case and transliteration "
            "as appropriate to the target language. Preserve intentionally "
            "unchanged names when justified. If a glossary decision conflicts "
            "with correct source-grounded target-language usage, report the "
            "glossary defect; never degrade the translation to force a literal match."
        )
    }, ensure_ascii=False)


def full_shared_context(book: Path) -> tuple[str, str]:
    glossary = (book / "glossary.md").read_text("utf-8")
    guide = (book / "style-guide.md").read_text("utf-8")
    if max(len(glossary), len(guide)) > MAX_SHARED_CHARS:
        raise ValueError("literary memory exceeds prompt budget; refuse silent truncation")
    return glossary, guide


def _cell(value: str) -> str:
    return value.replace("|", "\\|").strip()


def updated_glossary(current: str, rows: list[dict]) -> str:
    if not rows:
        return current
    if "| Original | Translation | Type | Notes |" not in current:
        raise ValueError("glossary template is not recognized")
    result = current.rstrip("\n")
    for row in rows:
        prefix = "| " + _cell(row["original"]) + " |"
        existing = [line for line in result.splitlines() if line.startswith(prefix)]
        target = f"| {_cell(row['original'])} | {_cell(row['translation'])} | {_cell(row['type'])} | {_cell(row['notes'])} |"
        if existing:
            if any(line.split("|")[2].strip() != _cell(row["translation"]) for line in existing):
                raise ValueError(f"conflicting glossary rendering for {row['original']}")
            continue
        result += "\n" + target
    return result + "\n"


def updated_style(current: str, rows: list[dict]) -> str:
    if not rows:
        return current
    heading = "## Automated reviewed stylistic decisions"
    header = "| Source pattern / context | Target-language approach | Reason / evidence |\n| --- | --- | --- |"
    result = current.rstrip("\n")
    if heading not in result:
        result += "\n\n" + heading + "\n\n" + header
    for row in rows:
        value = f"| {_cell(row['source_pattern'])} | {_cell(row['translation_approach'])} | {_cell(row['evidence'])} |"
        if value not in result.splitlines():
            result += "\n" + value
    return result + "\n"


def save_claim_bound_decisions(book: Path, unit_id: str, session_id: str,
                               target: str, items: list[dict]) -> int:
    """Use pinned Workflow's audited proposal and compare-and-swap reconciliation."""
    if not items:
        return 0
    if target not in {"glossary", "style_guide"}:
        raise ValueError("invalid literary memory target")
    sys.path.insert(0, str(book.parents[1] / "scripts"))
    from workflow.filesystem import FilesystemStorage
    from workflow.repository import WorkflowStateRepository
    from workflow.proposals import ProposalManager
    repository = WorkflowStateRepository(FilesystemStorage(book))
    manager = ProposalManager(repository)
    name = "glossary.md" if target == "glossary" else "style-guide.md"
    current = (book / name).read_text("utf-8")
    replacement = (updated_glossary(current, items) if target == "glossary"
                   else updated_style(current, items))
    if replacement == current:
        return 0
    proposal = manager.submit(
        unit_id, session_id=session_id, target=target,
        suggestion=json.dumps(items, ensure_ascii=False),
        rationale="Source-grounded literary decisions from the active worker.",
    )
    result = manager.reconcile(
        proposal.proposal["proposal_id"], session_id="automated-orchestrator-" + session_id,
        accept=True, replacement=replacement.encode("utf-8"),
    )
    if result.resolution["status"] != "accepted":
        raise ValueError("canonical literary memory proposal was not accepted: "
                         + result.resolution["status"])
    return len(items)


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
