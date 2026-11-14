#!/usr/bin/env python3
"""Inspect durable book jobs and deliver signed, idempotent n8n events.

This is an observation/notification adapter. It never changes book files,
translation/review claims, pinned workflow revisions, or job statuses.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("BOOK_TRANSLATOR_STATE_ROOT", os.getcwd())).resolve()
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SLUG = re.compile(r"^[\w-]{1,120}$", re.UNICODE)
FINAL = {"done", "blocked"}
STALE_HOURS = 2


def read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def recent_review_records(ledger: dict) -> dict:
    latest = {}
    for record in ledger.get("records", []):
        unit_id = record.get("unit_id")
        if unit_id and (unit_id not in latest or
                        record.get("sequence", 0) > latest[unit_id].get("sequence", 0)):
            latest[unit_id] = record
    return latest


def verify_done(root: Path, status: dict) -> tuple[int, int, str]:
    """Fail closed: a reported success is not proof of an accepted EPUB."""
    slug = status.get("book_slug", "")
    if not SLUG.fullmatch(slug) or slug in {".", ".."}:
        raise ValueError("invalid book slug")
    book = (root / "books" / slug).resolve()
    output = (book / "output").resolve()
    artifact_path = status.get("artifact_path", "")
    if not isinstance(artifact_path, str):
        raise ValueError("missing artifact path")
    artifact = (root / artifact_path).resolve()
    if not artifact.is_relative_to(output) or artifact.suffix.lower() != ".epub":
        raise ValueError("artifact is outside this book output directory")
    if not artifact.is_file():
        raise ValueError("EPUB artifact missing")
    manifest = read_json(output / "manifest.json")
    expected = status.get("artifact_sha256")
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise ValueError("missing or invalid artifact checksum")
    if (manifest.get("book_slug") != slug or
            manifest.get("artifact_sha256") != expected or
            (book / manifest.get("artifact_path", "")).resolve() != artifact):
        raise ValueError("output manifest and job status disagree")
    if digest(artifact) != expected:
        raise ValueError("EPUB SHA-256 mismatch")
    with zipfile.ZipFile(artifact) as archive:
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("invalid EPUB mimetype")
        if archive.testzip() is not None:
            raise ValueError("EPUB ZIP CRC failure")

    progress = read_json(book / "progress.json")
    ledger = read_json(book / "review-ledger.json")
    chapters = progress.get("chapters", [])
    if not chapters or manifest.get("unit_count") != len(chapters):
        raise ValueError("incomplete output unit count")
    latest = recent_review_records(ledger)
    preview = manifest.get("preview") is True
    if preview != (status.get("review_provenance") == "user-approved-unreviewed"):
        raise ValueError("preview provenance disagrees with manifest")
    approvals = {}
    if preview:
        approved = read_json(root / "automation" / "user-approvals" / (slug + ".json"))
        approvals = {u["unit_id"]: u for u in approved.get("units", [])}
        if approved.get("workflow_revision") != status.get("workflow_revision"):
            raise ValueError("user approval is bound to another workflow")
    reviewed = 0
    for unit in chapters:
        uid = unit["unit_id"]
        acceptance = unit.get("translation_acceptance") or {}
        record = latest.get(uid)
        if unit.get("status") == "reviewed":
            if not record or record.get("outcome") != "PASS":
                raise ValueError("reviewed unit lacks latest independent PASS")
            for field in ("source_sha256", "translation_sha256"):
                if record.get(field) != acceptance.get(field):
                    raise ValueError("review is stale for current translation")
            if (not record.get("reviewer_session_id") or
                    not acceptance.get("session_id") or
                    record.get("reviewer_session_id") == acceptance.get("session_id")):
                raise ValueError("translation was not independently reviewed")
            if any(not finding.get("resolved") for finding in
                   (record.get("quality_report") or {}).get("findings", [])):
                raise ValueError("review PASS has unresolved findings")
            reviewed += 1
        elif preview and unit.get("status") == "translated":
            match = approvals.get(uid) or {}
            if (not match or
                    match.get("source_sha256") != acceptance.get("source_sha256") or
                    match.get("translation_sha256") != acceptance.get("translation_sha256") or
                    (record is not None and record.get("outcome") == "CORRECTIONS_REQUIRED")):
                raise ValueError("preview unit lacks valid exact user approval")
        else:
            raise ValueError("unaccepted unit in finished book")
    return len(chapters), reviewed, "completed_preview" if preview else "completed"


def file_age(root: Path, rel: str, now: datetime) -> timedelta | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "--", rel],
        cwd=root, text=True, capture_output=True, check=False, timeout=15,
    )
    if result.returncode or not result.stdout.strip():
        return None
    try:
        return now - utc_date(result.stdout.strip().splitlines()[0])
    except ValueError:
        return None


def inspect(root: Path, job_id: str, now: datetime, include_missing: bool) -> dict | None:
    status_file = root / "automation" / "status" / (job_id + ".json")
    if not status_file.is_file():
        if not include_missing:
            return None
        rel = "intake/auto/" + job_id + "/request.json"
        if not (root / rel).is_file():
            return None
        age = file_age(root, rel, now)
        if age is None or age < timedelta(hours=STALE_HOURS):
            return None
        return {"job_id": job_id, "type": "stalled", "state": "missing",
                "phase": "status-not-created", "reason": "Job has no durable status"}
    try:
        status = read_json(status_file)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"job_id": job_id, "type": "integrity_failed", "state": "invalid",
                "phase": "malformed-status", "reason": "Status JSON is unreadable"}
    if status.get("job_id") != job_id:
        return {"job_id": job_id, "type": "integrity_failed", "state": "invalid",
                "phase": "identity-mismatch", "reason": "Status ID mismatch"}
    state = status.get("state")
    if state == "running":
        try:
            elapsed = now - utc_date(status["updated_at"])
        except (ValueError, KeyError, TypeError):
            elapsed = timedelta.max
        if elapsed < timedelta(hours=STALE_HOURS):
            return None
        kind = "stalled"
        reason = "No durable checkpoint for at least two hours; inspect Actions and claims"
    elif state == "blocked":
        # A retired intake request must not alert the same user twice.
        if status.get("superseded_by_job_id") or str(status.get("phase", "")).startswith("superseded-"):
            return None
        kind = "blocked"
        if status.get("phase") == "correction-limit-reached":
            reason = "Automatic correction limit reached; no further correction attempt is authorized for this unit."
        elif status.get("phase") == "review-deferred-requires-operator":
            reason = "All other chapters are processed; deferred chapters need a current PASS review before delivery."
        else:
            reason = status.get("last_error") or status.get("phase") or "Blocked"
    elif state == "done":
        try:
            total, reviewed, kind = verify_done(root, status)
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            kind = "integrity_failed"
            reason = "Reported done but verification failed: " + str(exc)[:180]
        else:
            reason = "Verified EPUB and review provenance"
    else:
        return None
    event = {"job_id": job_id, "type": kind, "state": state,
             "phase": str(status.get("phase", ""))[:100],
             "reason": str(reason)[:350],
             "book_slug": str(status.get("book_slug") or "")[:120],
             "blocked_unit_id": str(status.get("blocked_unit_id") or "")[:100],
             "correction_rounds": status.get("correction_rounds") or {},
             "failures": status.get("failures", 0),
             "status_updated_at": status.get("updated_at", "")}
    if status.get("deferred_review_units"):
        event["deferred_review_units"] = status["deferred_review_units"]
    if state == "done" and kind in {"completed", "completed_preview"}:
        event.update({"artifact_path": status["artifact_path"],
                      "artifact_sha256": status["artifact_sha256"],
                      "total_units": total, "reviewed_units": reviewed,
                      "review_provenance": status.get("review_provenance") or "independent"})
    if state == "blocked" and status.get("book_slug"):
        try:
            units = read_json(root / "books" / status["book_slug"] / "progress.json")["chapters"]
            event["total_units"] = len(units)
            event["reviewed_units"] = sum(u.get("status") == "reviewed" for u in units)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return event


def event_id(event: dict) -> str:
    if event.get("type") == "blocked":
        event = {k: v for k, v in event.items() if k not in {"reviewed_units", "total_units"}}
    data = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def legacy_blocked_receipt(event: dict, directory: Path) -> bool:
    if event.get("type") != "blocked" or not directory.is_dir():
        return False
    old = {k: v for k, v in event.items() if k != "event_id"}
    total = old.get("total_units")
    # shortcut: legacy matching assumes unchanged total units; remove after old receipts expire.
    candidates = [dict(old, reviewed_units=n) for n in range(total + 1)] if type(total) is int and total >= 0 else []
    candidates.append({k: v for k, v in old.items() if k not in {"reviewed_units", "total_units"}})
    for candidate in candidates:
        data = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if (directory / (hashlib.sha256(data.encode("utf-8")).hexdigest() + ".json")).is_file():
            return True
    return False


def send(url: str, secret: str, event: dict) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.fragment):
        raise ValueError("BOOK_EVENTS_WEBHOOK_URL must be an HTTPS webhook URL")
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = hmac.new(secret.encode("utf-8"),
                         timestamp.encode("ascii") + b"." + payload,
                         hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Book-Event-Id": event["event_id"],
                 "X-Book-Event-Timestamp": timestamp,
                 "X-Book-Event-Signature": "sha256=" + signature},
    )
    # Never follow redirects that might leak signed event payloads.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=15) as reply:
            if reply.status < 200 or reply.status >= 300:
                raise ValueError("n8n did not acknowledge event")
    except urllib.error.HTTPError as exc:
        raise ValueError("n8n webhook HTTP status " + str(exc.code)) from None
    except urllib.error.URLError:
        raise ValueError("n8n webhook connection failed") from None


def git_ack(root: Path) -> None:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=root, text=True,
                              capture_output=True, check=False, timeout=50)
    check = git("add", "-A", "--", "automation/notifications/sent")
    if check.returncode:
        raise RuntimeError("cannot stage notification receipts")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    if git("commit", "-m", "monitor: record delivered book notifications").returncode:
        raise RuntimeError("cannot commit delivery receipts")
    for attempt in range(5):
        if git("push", "origin", "HEAD:main").returncode == 0:
            return
        if git("fetch", "origin", "main").returncode:
            break
        if git("rebase", "origin/main").returncode:
            git("rebase", "--abort")
            break
    raise RuntimeError("webhook acknowledged but receipt publication failed; receiver must deduplicate event_id")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", help="Optional job id to inspect")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-missing", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.job_id and not JOB_ID.fullmatch(args.job_id):
        parser.error("job id must be a 32-character lowercase hex string")
    now = datetime.now(timezone.utc)
    if args.job_id:
        jobs = [args.job_id]
    else:
        status_dir = root / "automation" / "status"
        jobs = sorted(p.stem for p in status_dir.glob("*.json") if JOB_ID.fullmatch(p.stem))
        if args.include_missing:
            jobs = sorted(set(jobs) | {p.parent.name for p in
                          (root / "intake" / "auto").glob("*/request.json")
                          if JOB_ID.fullmatch(p.parent.name)})
    events = []
    for job in jobs:
        event = inspect(root, job, now, args.include_missing)
        if event is None:
            continue
        event["schema_version"] = 1
        event["event_id"] = event_id(event)
        receipt = root / "automation" / "notifications" / "sent" / job / (event["event_id"] + ".json")
        if receipt.exists() or legacy_blocked_receipt(event, receipt.parent):
            continue
        events.append((event, receipt))
    print(json.dumps({"pending_events": len(events),
                      "types": [e["type"] for e, _ in events]}, ensure_ascii=False))
    if args.dry_run:
        return 0
    url = os.environ.get("BOOK_EVENTS_WEBHOOK_URL", "").strip()
    secret = os.environ.get("BOOK_EVENTS_SIGNING_SECRET", "").strip()
    if not url or not secret:
        print("Webhook delivery not configured: set both BOOK_EVENTS_WEBHOOK_URL and BOOK_EVENTS_SIGNING_SECRET; no receipts recorded")
        return 0
    for event, receipt in events:
        send(url, secret, event)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({
            "schema_version": 1, "event_id": event["event_id"],
            "job_id": event["job_id"], "type": event["type"],
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Commit after each acknowledged event so failures do not hide earlier sends.
        git_ack(root)
        print("Delivered event " + event["event_id"] + " (" + event["type"] + ")")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print("Book monitor failed: " + str(exc)[:300], file=sys.stderr)
        raise SystemExit(1)
