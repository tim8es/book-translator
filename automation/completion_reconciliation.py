"""Reconcile immutable intake jobs with an already verified final book.

No model calls, book writes, source updates, claim cleanup or workflow upgrades.
Only exact current source identity is eligible; previews are not reconciled.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

import job_monitor

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
SLUG = re.compile(r"[^\W_][\w-]{0,99}\Z")
REVISIONS = {"metadata": "metadata.json", "progress": "progress.json",
             "review_ledger": "review-ledger.json"}


def exact_workspace(root: Path, request: dict) -> str | None:
    """Never choose between multiple source-identical workspaces."""
    matches = []
    for path in sorted((root / "books").glob("*/metadata.json")):
        if (not SLUG.fullmatch(path.parent.name) or path.parent.is_symlink()
                or path.is_symlink()):
            continue
        try:
            metadata = job_monitor.read_json(path)
            if ((metadata.get("source") or {}).get("sha256") == request.get("source_sha256")
                    and metadata.get("target_language") == request.get("target_language")
                    and isinstance(request.get("source_sha256"), str)
                    and SHA64.fullmatch(request["source_sha256"])):
                matches.append(path.parent.name)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return matches[0] if len(matches) == 1 else None


def _file(book: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("invalid book-relative path")
    rel = Path(relative)
    if ".." in rel.parts:
        raise ValueError("unsafe book path")
    path = book / rel
    if (not path.is_file() or path.is_symlink()
            or not path.resolve().is_relative_to(book.resolve())):
        raise ValueError("book evidence file missing or unsafe")
    return path


def verified_completion(root: Path, request: dict, status: dict | None,
                        slug: str) -> dict:
    """Return status fields only after current source, ledger and bytes agree.

    The pinned workflow has already produced the final artifact. Its manifest
    must bind the exact current metadata/progress/ledger bytes. No historical
    or unreviewed preview artifact can satisfy this check.
    """
    if not SLUG.fullmatch(slug):
        raise ValueError("invalid book slug")
    book = root / "books" / slug
    if book.is_symlink() or exact_workspace(root, request) != slug:
        raise ValueError("source identity is ambiguous or does not match")
    metadata = job_monitor.read_json(_file(book, "metadata.json"))
    source = metadata["source"]
    pin = metadata["workflow"]["resolved_revision"]
    source_sha = request["source_sha256"]
    if (not SHA40.fullmatch(pin) or not SHA64.fullmatch(source_sha)
            or source["sha256"] != source_sha
            or source.get("storage_mode") != "embedded"
            or metadata["target_language"] != request["target_language"]
            or request.get("workflow_revision", pin) != pin):
        raise ValueError("source or workflow identity mismatch")
    if status is not None:
        if (status.get("job_id") != request["job_id"]
                or status.get("state") not in {"running", "blocked", "paused", "done"}
                or status.get("book_slug") not in (None, slug)
                or status.get("source_sha256") not in (None, source_sha)
                or status.get("workflow_revision") not in (None, pin)
                or status.get("superseded_by_job_id")
                or str(status.get("phase", "")).startswith("superseded-")):
            raise ValueError("status belongs to another identity or is retired")
    original = _file(book, "source/" + source["filename"])
    if job_monitor.digest(original) != source_sha:
        raise ValueError("preserved source bytes changed")
    wf = book / ".workflow"
    if (any((wf / "claims").glob("*.json"))
            or any((wf / name).exists() for name in
                   ("coordination-lock.json", "finalization.json",
                    "source-promotion.json", "waves/active.json"))):
        raise ValueError("book has active work")
    manifest = job_monitor.read_json(_file(book, "output/manifest.json"))
    revisions = {key: job_monitor.digest(_file(book, name))
                 for key, name in REVISIONS.items()}
    if (manifest.get("book_slug") != slug or manifest.get("format") != "epub"
            or manifest.get("preview") is not False
            or manifest.get("state_revisions") != revisions):
        raise ValueError("output is not a current final reviewed EPUB")
    progress = job_monitor.read_json(_file(book, "progress.json"))
    corpus = job_monitor.read_json(_file(book, "source-manifest.json"))
    chapters = progress["chapters"]
    extracted = corpus["extracted"]
    if (progress.get("book_slug") != slug or corpus.get("source_sha256") != source_sha
            or not chapters or len(chapters) != len(extracted)
            or len(chapters) != metadata["chapter_count"]):
        raise ValueError("source corpus/progress mismatch")
    sources = {item["unit_id"]: item for item in extracted}
    if len(sources) != len(chapters):
        raise ValueError("duplicate source units")
    seen = set()
    for unit in chapters:
        uid = unit["unit_id"]
        entry = sources[uid]
        acceptance = unit["translation_acceptance"]
        source_hash = job_monitor.digest(_file(book, unit["source_path"]))
        target_hash = job_monitor.digest(_file(book, unit["translation_path"]))
        if (uid in seen or unit["status"] != "reviewed"
                or entry["path"] != unit["source_path"]
                or source_hash != entry["sha256"]
                or source_hash != acceptance["source_sha256"]
                or target_hash != acceptance["translation_sha256"]
                or acceptance["workflow_revision"] != pin):
            raise ValueError("unit is incomplete or its accepted bytes changed")
        seen.add(uid)
    artifact = _file(book, manifest["artifact_path"])
    if not artifact.resolve().is_relative_to((book / "output").resolve()):
        raise ValueError("artifact outside output")
    fields = {"book_slug": slug, "source_sha256": source_sha,
              "workflow_revision": pin, "state": "done",
              "phase": "reconciled-completed",
              "artifact_path": artifact.relative_to(root).as_posix(),
              "artifact_sha256": manifest["artifact_sha256"],
              "review_provenance": "independent", "active": None,
              "queue_snapshot": {"extracted": 0, "reviewed": len(chapters),
                                 "awaiting_review": 0, "correction_candidates": 0,
                                 "awaiting_promotion": 0}}
    total, reviewed, kind = job_monitor.verify_done(root, fields)
    if total != reviewed or kind != "completed":
        raise ValueError("incomplete review provenance")
    return fields


def candidate(root: Path, request: dict, status: dict | None) -> dict | None:
    if status and status.get("state") == "done":
        return None
    try:
        slug = exact_workspace(root, request)
        if slug is None:
            return None
        return verified_completion(root, request, status, slug)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile):
        return None


def reconciled_status(job_id: str, previous: dict | None, fields: dict) -> dict:
    result = dict(previous or {"schema_version": 1, "job_id": job_id,
                               "correction_rounds": {}, "failures": 0})
    result["completion_reconciliation"] = {
        "previous_state": result.get("state", "missing"),
        "previous_phase": result.get("phase"),
        "previous_updated_at": result.get("updated_at"),
        "previous_error": result.get("last_error"),
        "previous_active": result.get("active"),
    }
    for key in ("last_error", "last_error_code", "blocked_unit_id", "provider_retry",
                "preflight_retry_at", "deferred_review_units"):
        result.pop(key, None)
    result.update(fields)
    return result
