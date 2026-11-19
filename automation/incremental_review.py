"""Opt-in, hash-bound reuse of *unchanged clean* bounded review windows.

Only current source-owned marker chunks are eligible. All changed passages,
overlap contexts and previous unresolved findings require new independent
review. Canonical review recording and PASS validation remain authoritative.
No literary content is stored in the added checkpoint metadata.
"""
from __future__ import annotations

import os
import re
from collections import Counter
import review_chunks

STAGE_VERSION = 2
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BINDING_KEYS = {
    "previous_record_id", "glossary_sha256", "style_guide_sha256",
    "reviewer_contract_sha256",
}
META_KEYS = {
    "schema_version", "plan", "unit_id", "workflow_revision", "binding",
    "completed", "windows", "accepted_review_record_id", "reused",
}


def binding(previous_id: str | None, glossary_sha: str, style_sha: str,
            contract_sha: str) -> dict:
    if (previous_id is not None
            and (not isinstance(previous_id, str) or not previous_id
                 or len(previous_id) > 128)):
        raise ValueError("invalid prior reviewer record ID")
    for digest in (glossary_sha, style_sha, contract_sha):
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise ValueError("invalid literary snapshot digest")
    return {
        "previous_record_id": previous_id,
        "glossary_sha256": glossary_sha,
        "style_guide_sha256": style_sha,
        "reviewer_contract_sha256": contract_sha,
    }


def _validate_binding(value: dict) -> None:
    if not isinstance(value, dict) or set(value) != BINDING_KEYS:
        raise ValueError("review binding is missing or malformed")
    binding(value["previous_record_id"], value["glossary_sha256"],
            value["style_guide_sha256"], value["reviewer_contract_sha256"])


def new_stage(plan: dict, unit_id: str, workflow_revision: str, current_binding: dict) -> dict:
    if plan.get("helper_revision") not in review_chunks.PLAN_REVISIONS:
        raise ValueError("incremental reviewer requires protected marker alignment")
    _validate_binding(current_binding)
    return {
        "schema_version": STAGE_VERSION, "plan": plan,
        "unit_id": unit_id, "workflow_revision": workflow_revision,
        "binding": dict(current_binding),
        "completed": {}, "windows": {},
        "accepted_review_record_id": None, "reused": {},
    }


def window_hashes(chunk: dict, source: str, translation: str) -> dict:
    return {
        "source_sha256": review_chunks.sha(source[slice(*chunk["source_window"])]),
        "translation_sha256": review_chunks.sha(
            translation[slice(*chunk["translation_window"])]),
    }


def validate_shape(stage: dict, unit_id: str, revision: str) -> None:
    """Strict persisted schema, even when the old target bytes are unavailable."""
    if (not isinstance(stage, dict) or set(stage) != META_KEYS
            or stage.get("schema_version") != STAGE_VERSION
            or stage.get("unit_id") != unit_id
            or stage.get("workflow_revision") != revision):
        raise ValueError("incremental review checkpoint is invalid")
    _validate_binding(stage["binding"])
    plan = stage["plan"]
    if (not isinstance(plan, dict)
            or set(plan) != review_chunks.plan_fields(plan)
            or plan["schema_version"] != review_chunks.SCHEMA_VERSION
            or plan["helper_revision"] not in review_chunks.PLAN_REVISIONS
            or not all(isinstance(plan[key], str) and HEX64.fullmatch(plan[key])
                       for key in ("source_sha256", "translation_sha256"))
            or not isinstance(plan["chunks"], list)
            or not 2 <= len(plan["chunks"]) <= review_chunks.MAX_CHUNKS):
        raise ValueError("incremental review plan is invalid")
    ids = set()
    for i, chunk in enumerate(plan["chunks"], 1):
        if (not isinstance(chunk, dict)
                or set(chunk) != {"id", "source_core", "translation_core",
                                  "source_window", "translation_window"}
                or chunk["id"] != f"chunk-{i:03d}"):
            raise ValueError("incremental review chunk is invalid")
        ids.add(chunk["id"])
        for field in ("source_core", "translation_core",
                      "source_window", "translation_window"):
            interval = chunk[field]
            if (not isinstance(interval, list) or len(interval) != 2
                    or any(type(n) is not int or n < 0 for n in interval)
                    or interval[0] >= interval[1]):
                raise ValueError("incremental review interval is invalid")
    completed, windows, reused = (
        stage["completed"], stage["windows"], stage["reused"])
    if (not isinstance(completed, dict) or not isinstance(windows, dict)
            or not isinstance(reused, dict)
            or not set(reused) <= set(completed)
            or set(completed) != set(windows)
            or not set(completed) <= ids):
        raise ValueError("incremental review evidence keys are inconsistent")
    for chunk_id, item in completed.items():
        if (not isinstance(item, dict)
                or set(item) not in ({"outcome", "report", "style"},
                                     {"outcome", "report", "style", "glossary"})
                or item["outcome"] not in {"PASS", "CORRECTIONS_REQUIRED"}
                or not isinstance(item["report"], dict)
                or not isinstance(item["style"], list)
                or ("glossary" in item and not isinstance(item["glossary"], list))):
            raise ValueError("incremental review part evidence is invalid")
        hashes = windows[chunk_id]
        if (not isinstance(hashes, dict)
                or set(hashes) != {"source_sha256", "translation_sha256"}
                or any(not isinstance(value, str) or not HEX64.fullmatch(value)
                       for value in hashes.values())):
            raise ValueError("incremental review window hashes are invalid")
    for key, item in reused.items():
        if (not isinstance(item, dict)
                or set(item) != {"from_translation_sha256", "from_record_id"}
                or not isinstance(item["from_translation_sha256"], str)
                or not HEX64.fullmatch(item["from_translation_sha256"])
                or (item["from_record_id"] is not None
                    and not isinstance(item["from_record_id"], str))):
            raise ValueError("invalid incremental review provenance")
    review_id = stage["accepted_review_record_id"]
    if review_id is not None and (not isinstance(review_id, str) or not review_id
                                  or len(review_id) > 128
                                  or len(completed) != len(plan["chunks"])):
        raise ValueError("incomplete reviewer stage cannot claim a recorded review")


def validate_current(stage: dict, plan: dict, source: str,
                     translation: str, current_binding: dict,
                     unit_id: str, revision: str) -> None:
    validate_shape(stage, unit_id, revision)
    if stage["plan"] != plan or stage["binding"] != current_binding:
        raise ValueError("incremental review stage does not match current artifacts")
    review_chunks.validate_plan(plan, source, translation)
    for part in plan["chunks"]:
        if part["id"] in stage["completed"] and stage["windows"][part["id"]] != window_hashes(
                part, source, translation):
            raise ValueError("incremental review window hash was altered")


def record_chunk(stage: dict, part: dict, source: str, translation: str) -> None:
    if part["id"] not in stage["completed"]:
        raise ValueError("cannot fingerprint a missing reviewer part")
    stage["windows"][part["id"]] = window_hashes(part, source, translation)
    stage["reused"].pop(part["id"], None)


def _old_report_matches(stage: dict, previous: dict | None) -> bool:
    """An archived full review must match the exact accepted ledger record."""
    if (not isinstance(previous, dict)
            or stage["accepted_review_record_id"] != previous.get("record_id")
            or previous.get("outcome") != "CORRECTIONS_REQUIRED"
            or previous.get("source_sha256") != stage["plan"]["source_sha256"]
            or previous.get("translation_sha256") != stage["plan"]["translation_sha256"]):
        return False
    quality = previous.get("quality_report")
    if not isinstance(quality, dict) or quality.get("evidence_version") != 2:
        return False
    try:
        aggregate = review_chunks.combine(stage)
    except (ValueError, TypeError, KeyError):
        return False
    return (aggregate["outcome"] == previous["outcome"]
            and aggregate["findings"] == quality.get("findings")
            and aggregate["checks"] == quality.get("checks"))


def rebase(stage: dict, new_plan: dict, source: str, translation: str,
           current_binding: dict, previous: dict | None,
           unit_id: str, revision: str, *, audit: dict | None = None) -> tuple[dict, int]:
    """Return fresh stage, reusing only exact, clean, context-identical windows.

    A saved complete stage is reusable only if the canonical *last rejected*
    ledger record matches the exact previous full review. An unfinished stage
    can reuse evidence only if its original previous review is unchanged.
    """
    validate_shape(stage, unit_id, revision)
    _validate_binding(current_binding)
    review_chunks.validate_plan(new_plan, source, translation)
    fresh = new_stage(new_plan, unit_id, revision, current_binding)
    reasons = Counter()
    def finish(reason=None):
        if reason:
            reasons[reason] += len(new_plan["chunks"])
        if audit is not None:
            audit.update({"reused": len(fresh["completed"]), "reasons": dict(reasons)})
        return fresh, len(fresh["completed"])

    if stage["plan"]["source_sha256"] != new_plan["source_sha256"]:
        return finish("source_changed")
    if stage["plan"]["translation_sha256"] == new_plan["translation_sha256"]:
        return finish("same_artifact")
    for key in ("glossary_sha256", "style_guide_sha256", "reviewer_contract_sha256"):
        if stage["binding"][key] != current_binding[key]:
            return finish(key + "_changed")
    archive_record = stage["accepted_review_record_id"]
    if archive_record is not None:
        if not _old_report_matches(stage, previous):
            return finish("archive_ledger_mismatch")
    elif stage["binding"]["previous_record_id"] != current_binding["previous_record_id"]:
        return finish("previous_record_changed")

    reuse_closed = (os.environ.get("BOOK_TRANSLATOR_REUSE_CLOSED_REVIEW") == "on"
                    and archive_record is not None
                    and previous["quality_report"].get("context") == "independent_session")
    if reuse_closed:
        # Do not reuse local literary judgments across known nonlocal risks.
        # The prior full record remains immutable, and ordinary full comparison
        # is still required for changed shared memory or cross-scope effects.
        for finding in previous["quality_report"]["findings"]:
            if not finding["resolved"] and (
                    finding["category"] in {"structure", "voice", "terminology"}
                    or finding["severity"] == "critical"
                    or (finding["severity"] != "minor"
                        and finding["category"] in {"referent", "register", "style"})):
                return finish("nonlocal_prior_finding")
    old_by_id = {item["id"]: item for item in stage["plan"]["chunks"]}
    mapped = (review_chunks.source_owned_map(stage["plan"]) is not None
              and review_chunks.source_owned_map(new_plan) is not None)
    old_by_source = {(tuple(item["source_core"]),tuple(item["source_window"])):item
                     for item in stage["plan"]["chunks"]} if mapped else {}
    def relative_target_core(item):
        start=item["translation_window"][0]
        return [i-start for i in item["translation_core"]]
    for part in new_plan["chunks"]:
        key=part["id"]
        old=(old_by_source.get((tuple(part["source_core"]),tuple(part["source_window"])))
             if mapped else old_by_id.get(key))
        old_key=old["id"] if old is not None else None
        evidence=stage["completed"].get(old_key)
        if old is None or evidence is None:
            reasons["no_matching_window"] += 1
            continue
        if evidence["outcome"] != "PASS":
            reasons["prior_window_rejected"] += 1
            continue
        findings = evidence["report"].get("findings")
        if findings != [] and not (
                reuse_closed and isinstance(findings, list)
                and all(isinstance(f, dict) and f.get("resolved") is True
                        and isinstance(f.get("resolves"), dict) for f in findings)):
            reasons["prior_findings_not_reusable"] += 1
            continue
        if (old["source_core"] != part["source_core"]
                or old["source_window"] != part["source_window"]
                or relative_target_core(old) != relative_target_core(part)
                or stage["windows"][old_key] != window_hashes(part,source,translation)):
            reasons["window_or_context_changed"] += 1
            continue
        # Every *current* previous unresolved finding must be freshly checked,
        # even if it is in an otherwise byte-identical source/target window.
        try:
            previous_findings = review_chunks.find_previous_for_chunk(
                previous, part, source)
        except (ValueError, KeyError, TypeError):
            reasons["prior_anchor_invalid"] += 1
            continue
        if previous_findings:
            reasons["current_prior_recheck_required"] += 1
            continue
        if findings:
            # Old resolved rechecks have already been stored in the exact
            # canonical previous record. Replaying them would refer to an
            # older record and incorrectly claim new rechecks. Retain the
            # observed clean window and its archive provenance, not the old
            # recheck events. No omitted current finding can pass this branch.
            evidence = {**evidence, "report": {**evidence["report"], "findings": []}}
        fresh["completed"][key] = evidence
        fresh["windows"][key] = stage["windows"][old_key]
        fresh["reused"][key] = {
            "from_translation_sha256": stage["plan"]["translation_sha256"],
            "from_record_id": archive_record,
        }
    return finish()


def refine_current(stage,new_plan,source,translation,current_binding,unit_id,revision):
    """Same accepted bytes and context only. Preserve exact already-reviewed
    windows, including their real findings and prior lineage. New split parts
    never inherit evidence. No reuse across changed artifacts here.
    """
    validate_current(stage,stage["plan"],source,translation,current_binding,unit_id,revision)
    review_chunks.validate_plan(new_plan,source,translation)
    if stage["accepted_review_record_id"] is not None:
        raise ValueError("cannot refine recorded review")
    if new_plan["helper_revision"]!=review_chunks.REFINED_REVISION:
        raise ValueError("expected deterministic refined review plan")
    fresh=new_stage(new_plan,unit_id,revision,current_binding)
    def identity(part):
        return tuple(tuple(part[k]) for k in ("source_core","translation_core","source_window","translation_window"))
    by_window={identity(p):p for p in new_plan["chunks"]}
    for old in stage["plan"]["chunks"]:
        key=old["id"]
        if key not in stage["completed"]:
            continue
        match=by_window.get(identity(old))
        if match is None:
            raise ValueError("refinement would discard completed reviewer evidence")
        dest=match["id"]
        fresh["completed"][dest]=stage["completed"][key]
        fresh["windows"][dest]=stage["windows"][key]
        if key in stage["reused"]:
            fresh["reused"][dest]=stage["reused"][key]
    validate_current(fresh,new_plan,source,translation,current_binding,unit_id,revision)
    return fresh
