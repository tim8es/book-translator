"""Explicit, audited reopening of changed, previously reviewed translation units.

This command never produces translation acceptance or Reviewer PASS. It proves
that the old reviewed state belonged to unchanged source bytes, and that the
operator-approved candidate translation has the exact Git blob identity from
the plan, before atomically demoting only those units to "extracted".

The old append-only review ledger is retained. A new translator claim and
hash-bound acceptance, followed by an independent Reviewer PASS, are required
before any demoted unit can become reviewed or be used for final output.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .claim_cli import _book_directory
from .filesystem import FilesystemStorage
from .repository import WorkflowStateRepository
from .schemas import SchemaKind
from .storage import StorageNotFound
from .upgrade_evidence import adopted_acceptance, adopted_pass, read_upgrade_bridge

PLAN = ".workflow/editorial-reopen.json"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
UNIT = re.compile(r"^chapter-[0-9]{6}$")
ID = re.compile(r"^[0-9a-f]{32}$")


class EditorialReopenError(RuntimeError):
    """Operator-authorized migration or its evidence is invalid."""


def _blob_sha(content: bytes) -> str:
    prefix = b"blob " + str(len(content)).encode("ascii") + b"\x00"
    return hashlib.sha1(prefix + content).hexdigest()


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise EditorialReopenError(reason)


def reopen_reviewed(root: Path, slug: str, *, dry_run: bool = False) -> dict[str, Any]:
    book = _book_directory(root, slug)
    repository = WorkflowStateRepository(FilesystemStorage(book))
    storage = repository.storage
    metadata = repository.read("metadata.json", SchemaKind.METADATA).data

    try:
        raw_plan = storage.read(PLAN).content
        plan = json.loads(raw_plan.decode("utf-8"))
    except (StorageNotFound, ValueError, UnicodeError) as exc:
        raise EditorialReopenError("missing or malformed editorial reopen plan") from exc
    required = {
        "schema_version", "kind", "book_slug", "source_workflow_revision",
        "target_workflow_revision", "source_book_sha256",
        "baseline_progress_blob_sha", "baseline_ledger_blob_sha", "units",
    }
    _require(isinstance(plan, dict) and set(plan) == required,
             "unexpected editorial reopen plan shape")
    _require(type(plan["schema_version"]) is int and plan["schema_version"] == 1
             and plan["kind"] == "reopen-edited-reviewed-units",
             "unsupported editorial reopen plan")
    _require(plan["book_slug"] == slug, "editorial reopen book mismatch")
    old, target = plan["source_workflow_revision"], plan["target_workflow_revision"]
    _require(isinstance(old, str) and COMMIT.fullmatch(old) is not None
             and isinstance(target, str) and COMMIT.fullmatch(target) is not None
             and old != target and metadata.get("workflow", {}).get("resolved_revision") == target,
             "editorial reopen workflow pin mismatch")
    _require(plan["source_book_sha256"] == metadata.get("source", {}).get("sha256")
             and isinstance(plan["source_book_sha256"], str)
             and SHA.fullmatch(plan["source_book_sha256"]) is not None,
             "editorial reopen source identity mismatch")
    _require(all(isinstance(plan[k], str) and COMMIT.fullmatch(plan[k])
                 for k in ("baseline_progress_blob_sha", "baseline_ledger_blob_sha")),
             "editorial reopen baseline blob identity missing")
    _require(isinstance(plan["units"], list) and bool(plan["units"]),
             "editorial reopen has no selected units")
    _require(len(plan["units"]) <= 10000, "editorial reopen unit count is too large")

    # Migration may start with deliberately stale current translation acceptance.
    # Never disable eager artifact validation without independently verifying
    # every unit's source and old/new identity below.
    progress_blob = storage.read("progress.json").content
    ledger_blob = storage.read("review-ledger.json").content
    _require(_blob_sha(progress_blob) == plan["baseline_progress_blob_sha"],
             "editorial reopen progress baseline changed")
    _require(_blob_sha(ledger_blob) == plan["baseline_ledger_blob_sha"],
             "editorial reopen ledger baseline changed")
    progress_read = repository.read("progress.json", SchemaKind.PROGRESS,
                                    verify_artifacts=False)
    progress = progress_read.data
    ledger = repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER).data
    _require(progress.get("book_slug") == slug and ledger.get("book_slug") == slug,
             "editorial reopen book state mismatch")

    # This is an upgrade, not silent repinning: unchanged historical PASS
    # evidence must already have an audited exact-identity adoption bridge.
    bridge = read_upgrade_bridge(storage, metadata)
    _require(bridge is not None
             and bridge["source_workflow_revision"] == old
             and bridge["target_workflow_revision"] == target
             and bridge["pre_upgrade_progress_blob_sha"] == plan["baseline_progress_blob_sha"]
             and bridge["pre_upgrade_review_ledger_blob_sha"] == plan["baseline_ledger_blob_sha"],
             "editorial reopen requires a matching workflow-upgrade bridge")
    _require(not storage.list(".workflow/claims"), "active claims block editorial reopen")
    for path in (".workflow/finalization.json", ".workflow/source-promotion.json",
                 ".workflow/coordination-lock.json", ".workflow/waves/active.json"):
        try:
            storage.read(path)
        except StorageNotFound:
            continue
        raise EditorialReopenError(f"active coordination barrier blocks editorial reopen: {path}")

    targets: dict[str, dict[str, str]] = {}
    fields = {"unit_id", "source_sha256", "original_translation_sha256",
              "candidate_translation_blob_sha", "original_pass_record_id"}
    for entry in plan["units"]:
        _require(isinstance(entry, dict) and set(entry) == fields,
                 "invalid editorial reopen unit entry shape")
        uid = entry["unit_id"]
        _require(isinstance(uid, str) and UNIT.fullmatch(uid) is not None
                 and uid not in targets, "duplicate or malformed editorial reopen unit")
        _require(all(isinstance(entry[k], str) and SHA.fullmatch(entry[k]) is not None
                     for k in ("source_sha256", "original_translation_sha256")),
                 "editorial reopen source or acceptance hash is malformed")
        _require(isinstance(entry["candidate_translation_blob_sha"], str)
                 and COMMIT.fullmatch(entry["candidate_translation_blob_sha"]) is not None
                 and isinstance(entry["original_pass_record_id"], str)
                 and ID.fullmatch(entry["original_pass_record_id"]) is not None,
                 "editorial reopen candidate/pass identity malformed")
        targets[uid] = entry

    chapters = progress["chapters"]
    _require(len({c["unit_id"] for c in chapters}) == len(chapters),
             "editorial reopen contains duplicate progress units")
    _require(set(targets).issubset({c["unit_id"] for c in chapters}),
             "editorial reopen references unknown units")
    _require(len(bridge["entries"]) == len(chapters) - len(targets)
             and {e["unit_id"] for e in bridge["entries"]} ==
                 {c["unit_id"] for c in chapters} - set(targets),
             "historical reuse bridge must contain exactly unchanged units")

    records_by_unit: dict[str, list[dict[str, Any]]] = {}
    for record in ledger["records"]:
        records_by_unit.setdefault(record["unit_id"], []).append(record)

    changed = copy.deepcopy(progress)
    for chapter in changed["chapters"]:
        uid = chapter["unit_id"]
        acceptance = chapter.get("translation_acceptance")
        _require(chapter["status"] == "reviewed" and isinstance(acceptance, dict)
                 and acceptance.get("unit_id") == uid
                 and acceptance.get("workflow_revision") == old,
                 f"editorial reopen requires original reviewed acceptance: {uid}")
        source = storage.read(chapter["source_path"]).content
        translation = storage.read(chapter["translation_path"]).content
        _require(acceptance["source_sha256"] == _sha(source),
                 f"editorial reopen source drift: {uid}")
        history = records_by_unit.get(uid, [])
        _require(bool(history), f"editorial reopen missing historical review: {uid}")
        last = history[-1]
        _require(last.get("outcome") == "PASS"
                 and last.get("unit_id") == uid
                 and last.get("workflow_revision") == old
                 and last.get("review_contract_revision") == "docs/TRANSLATION.md@" + old
                 and last.get("source_sha256") == acceptance["source_sha256"]
                 and last.get("translation_sha256") == acceptance["translation_sha256"],
                 f"editorial reopen historical PASS mismatch: {uid}")
        if uid in targets:
            item = targets[uid]
            _require(item["source_sha256"] == acceptance["source_sha256"]
                     and item["original_translation_sha256"] == acceptance["translation_sha256"]
                     and item["original_pass_record_id"] == last["record_id"]
                     and item["candidate_translation_blob_sha"] == _blob_sha(translation),
                     f"editorial reopen selected unit evidence mismatch: {uid}")
            _require(_sha(translation) != acceptance["translation_sha256"],
                     f"editorial reopen selected unit is unchanged: {uid}")
            # The old PASS remains in the ledger as history, NOT as current
            # evidence for the new content.
            chapter["status"] = "extracted"
            chapter.pop("translation_acceptance", None)
        else:
            _require(_sha(translation) == acceptance["translation_sha256"],
                     f"editorial reopen unselected translation changed: {uid}")
            _require(adopted_acceptance(storage, metadata, chapter) is not None,
                     f"editorial reopen unselected translation is not bridged: {uid}")
            _require(adopted_pass(storage, metadata, chapter, history,
                                  acceptance["source_sha256"],
                                  acceptance["translation_sha256"]) is not None,
                     f"editorial reopen unselected PASS is not bridged: {uid}")

    result: dict[str, Any] = {
        "book_slug": slug, "workflow_revision": target,
        "reopened": sorted(targets), "reused_unchanged": len(chapters) - len(targets),
        "dry_run": dry_run,
    }
    if not dry_run:
        result["progress_revision"] = repository.write_if_version(
            "progress.json", SchemaKind.PROGRESS, changed, progress_read.version,
        )
    return result


def editorial_reopen_command(args: argparse.Namespace, root: Path) -> int:
    result = reopen_reviewed(root, args.slug, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def register_editorial_reopen_command(subparsers: argparse._SubParsersAction,
                                     root: Path) -> None:
    command = subparsers.add_parser(
        "reopen-reviewed",
        help="Operator-approved, hash-bound reopening of changed reviewed units.",
    )
    command.add_argument("slug", help="Book slug under books/.")
    command.add_argument("--dry-run", action="store_true")
    command.set_defaults(func=lambda args: editorial_reopen_command(args, root))
