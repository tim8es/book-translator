"""Deterministic generated review-report snapshots for Workflow v2."""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .reviews import REVIEW_CONTRACT_PATH, ReviewLedgerManager, ReviewResolution


REPORT_SCHEMA = "review-report-v1"
REVIEW_STATES = (
    "pass",
    "corrections_required",
    "missing",
    "stale",
    "untranslated",
)


def _workflow_identity(metadata: Mapping[str, Any]) -> tuple[str, str]:
    workflow = metadata.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValueError("metadata workflow is unavailable")
    revision = workflow.get("resolved_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("metadata workflow resolved_revision is unavailable")
    return revision, f"{REVIEW_CONTRACT_PATH}@{revision}"


def _duplicate_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("unit_id"),
        record.get("source_sha256"),
        record.get("translation_sha256"),
        record.get("workflow_revision"),
        record.get("review_contract_revision"),
        record.get("outcome"),
    )


def _record_matches_current_identity(
    record: Mapping[str, Any],
    resolution: ReviewResolution,
    *,
    workflow_revision: str,
    contract_revision: str,
) -> bool:
    return (
        resolution.translation_sha256 is not None
        and record.get("source_sha256") == resolution.source_sha256
        and record.get("translation_sha256") == resolution.translation_sha256
        and record.get("workflow_revision") == workflow_revision
        and record.get("review_contract_revision") == contract_revision
    )


def _history_payload(
    resolution: ReviewResolution,
    *,
    workflow_revision: str,
    contract_revision: str,
) -> tuple[list[dict[str, Any]], int]:
    current_id = (
        resolution.current_record.get("record_id")
        if isinstance(resolution.current_record, Mapping)
        else None
    )
    first_equivalent: dict[tuple[Any, ...], str] = {}
    history: list[dict[str, Any]] = []
    duplicate_count = 0

    for stored in resolution.history:
        record = copy.deepcopy(stored)
        record_id = record["record_id"]
        key = _duplicate_key(record)
        duplicate_of = first_equivalent.get(key)
        if duplicate_of is None:
            first_equivalent[key] = record_id
        else:
            duplicate_count += 1

        if record_id == current_id:
            classification = "current"
        elif _record_matches_current_identity(
            record,
            resolution,
            workflow_revision=workflow_revision,
            contract_revision=contract_revision,
        ):
            classification = "superseded"
        else:
            classification = "stale"

        record["classification"] = classification
        record["duplicate_of_record_id"] = duplicate_of
        history.append(record)

    return history, duplicate_count


def build_review_report_snapshot(
    manager: ReviewLedgerManager,
    progress: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Build deterministic report data from current artifacts and review ledger state."""

    workflow_revision, contract_revision = _workflow_identity(metadata)
    resolutions = manager.resolve_all(progress, metadata)
    counts = Counter(item.state for item in resolutions)
    duplicate_records = 0
    units: list[dict[str, Any]] = []

    for resolution in resolutions:
        history, unit_duplicates = _history_payload(
            resolution,
            workflow_revision=workflow_revision,
            contract_revision=contract_revision,
        )
        duplicate_records += unit_duplicates
        units.append(
            {
                "unit_id": resolution.unit_id,
                "chapter_number": resolution.chapter_number,
                "state": resolution.state,
                "source_sha256": resolution.source_sha256,
                "translation_sha256": resolution.translation_sha256,
                "current_review": (
                    copy.deepcopy(resolution.current_record)
                    if resolution.current_record is not None
                    else None
                ),
                "history": history,
            }
        )

    total = len(resolutions)
    passed = counts["pass"]
    percent = round((passed * 100.0 / total), 2) if total else 100.0
    summary = {
        "total_units": total,
        "pass": passed,
        "corrections_required": counts["corrections_required"],
        "missing": counts["missing"],
        "stale": counts["stale"],
        "untranslated": counts["untranslated"],
        "pass_coverage": {
            "passed": passed,
            "total": total,
            "percent": percent,
        },
        "duplicate_records": duplicate_records,
    }

    return {
        "schema": REPORT_SCHEMA,
        "book_slug": progress.get("book_slug"),
        "summary": summary,
        "units": units,
    }


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "-"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_review_report_markdown(snapshot: Mapping[str, Any]) -> str:
    """Render a deterministic Markdown audit view of a review-report snapshot."""

    summary = snapshot["summary"]
    coverage = summary["pass_coverage"]
    lines = [
        f"# Review Report — {snapshot['book_slug']}",
        "",
        "Generated from authoritative `review-ledger.json`, `progress.json`, current artifact bytes, and workflow revision.",
        "",
        f"PASS coverage: **{coverage['passed']}/{coverage['total']} ({coverage['percent']}%)**",
        "",
        "## Summary",
        "",
        f"- `pass`: {summary['pass']}",
        f"- `corrections_required`: {summary['corrections_required']}",
        f"- `missing`: {summary['missing']}",
        f"- `stale`: {summary['stale']}",
        f"- `untranslated`: {summary['untranslated']}",
        f"- duplicate review records: {summary['duplicate_records']}",
        "",
        "## Units",
        "",
        "| Unit | State | Source SHA-256 | Translation SHA-256 | Current outcome | Workflow revision | Review contract revision | Review commit |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for unit in snapshot["units"]:
        current = unit["current_review"] or {}
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_cell(unit["unit_id"]),
                    f"`{_markdown_cell(unit['state'])}`",
                    _markdown_cell(unit["source_sha256"]),
                    _markdown_cell(unit["translation_sha256"]),
                    _markdown_cell(current.get("outcome")),
                    _markdown_cell(current.get("workflow_revision")),
                    _markdown_cell(current.get("review_contract_revision")),
                    _markdown_cell(current.get("review_commit")),
                )
            )
            + " |"
        )

    lines.extend(("", "## History", ""))
    history_found = False
    for unit in snapshot["units"]:
        if not unit["history"]:
            continue
        history_found = True
        lines.extend((f"### {unit['unit_id']}", ""))
        for record in unit["history"]:
            duplicate_of = record.get("duplicate_of_record_id")
            duplicate = f"`{duplicate_of}`" if duplicate_of is not None else "-"
            lines.append(
                "- "
                f"sequence={record['sequence']} "
                f"outcome=`{record['outcome']}` "
                f"classification=`{record['classification']}` "
                f"duplicate_of={duplicate} "
                f"source_sha256=`{record['source_sha256']}` "
                f"translation_sha256=`{record['translation_sha256']}` "
                f"workflow_revision=`{record['workflow_revision']}` "
                f"review_contract_revision=`{record['review_contract_revision']}` "
                f"review_commit=`{record['review_commit'] or '-'}` "
                f"reviewed_at=`{record['reviewed_at']}`"
            )
        lines.append("")

    if not history_found:
        lines.append("No review records.")

    return "\n".join(lines).rstrip() + "\n"
