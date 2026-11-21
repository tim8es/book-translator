"""Book-scoped, SHA-bound executor admission for independent Auto and Pi lanes.

Missing marker means legacy Auto. Pi is explicit and durable, and its record
is copied into the book workspace on initialization. These markers do not
change the literary workflow revision or the ChatOps operator mode.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
EXECUTORS = frozenset({"auto", "pi"})


class OwnershipError(ValueError):
    """Invalid, missing or inconsistent execution lease."""


def _load(path: Path) -> dict | None:
    if path.is_symlink():
        raise OwnershipError("executor marker cannot be a symlink")
    if not path.exists():
        return None
    if not path.is_file():
        raise OwnershipError("executor marker is not a regular file")
    try:
        record = json.loads(path.read_text("utf-8"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise OwnershipError("executor marker is unreadable") from exc
    if not isinstance(record, dict):
        raise OwnershipError("executor marker must be a JSON object")
    return record


def job_marker_path(root: Path, job_id: str) -> Path:
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise OwnershipError("executor job identifier is invalid")
    return root / "intake" / "auto" / job_id / "executor.json"


def book_marker_path(root: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise OwnershipError("executor book slug is invalid")
    return root / "books" / slug / ".workflow" / "executor.json"


def marker(job_id: str, source_sha256: str, executor: str, slug: str | None = None) -> dict:
    if not isinstance(source_sha256, str) or not HEX.fullmatch(source_sha256):
        raise OwnershipError("executor source SHA-256 is invalid")
    if not isinstance(executor, str) or executor not in EXECUTORS:
        raise OwnershipError("unsupported executor")
    record = {"schema_version": 1, "job_id": job_id,
              "source_sha256": source_sha256, "executor": executor}
    if slug is not None:
        book_marker_path(Path("."), slug)  # Validate slug before serializing
        record["book_slug"] = slug
    job_marker_path(Path("."), job_id)     # Validate job ID before serializing
    return record


def selected(root: Path, job_id: str, source_sha256: str,
             slug: str | None = None) -> str:
    """Validate the job marker and, when present, its book-bound mirror."""
    marker(job_id, source_sha256, "auto")  # Validate identity even with no marker.
    job_record = _load(job_marker_path(root, job_id))
    if job_record is None:
        requested = "auto"
    else:
        requested = job_record.get("executor")
        if not isinstance(requested, str) or requested not in EXECUTORS or job_record != marker(
                job_id, source_sha256, requested):
            raise OwnershipError("executor job marker does not bind to immutable intake")
    if slug is not None:
        book_path = book_marker_path(root, slug)
        book_record = _load(book_path)
        if book_record is not None:
            if (not isinstance(book_record.get("executor"), str)
                    or book_record["executor"] not in EXECUTORS
                    or book_record != marker(
                        job_id, source_sha256, book_record["executor"], slug)
                    or book_record["executor"] != requested):
                raise OwnershipError("book executor marker conflicts with intake")
        elif requested == "pi" and book_path.parent.parent.exists():
            raise OwnershipError("Pi ownership requires a matching book marker")
    return requested


def require(root: Path, job_id: str, source_sha256: str, *,
            slug: str | None = None, backend: str = "omniroute") -> bool:
    """Return False for a stale worker; never mutate book state."""
    if backend not in {"omniroute", "pi"}:
        raise OwnershipError("invalid execution backend")
    return selected(root, job_id, source_sha256, slug) == (
        "pi" if backend == "pi" else "auto")


def write_book_marker(root: Path, job_id: str, source_sha256: str,
                      slug: str) -> bool:
    """During NEW book initialization only, within canonical book checkpoint."""
    if selected(root, job_id, source_sha256) != "pi":
        return False
    path = book_marker_path(root, slug)
    if path.exists():
        if selected(root, job_id, source_sha256, slug) != "pi":
            raise OwnershipError("book executor marker mismatch")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker(job_id, source_sha256, "pi", slug),
                               sort_keys=True) + "\n", encoding="utf-8")
    return True
