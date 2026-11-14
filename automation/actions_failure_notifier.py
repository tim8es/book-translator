#!/usr/bin/env python3
"""Recover GitHub Actions startup failures via the existing n8n admin callback.

The normal process job calls N8N_DELIVERY_CALLBACK_URL on terminal failures,
but a startup_failure has no jobs and cannot call that webhook. This observer
runs independently in book-job-monitor and records durable Git receipts.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_monitor import git_ack

ROOT = Path(os.environ.get("BOOK_TRANSLATOR_STATE_ROOT", os.getcwd())).resolve()
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
INTAKE_PATH = re.compile(r"^intake/auto/([0-9a-f]{32})/request[.]json$")
STARTUP_CONCLUSIONS = {"startup_failure", "action_required"}
LOOKBACK = timedelta(hours=36)


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def recent_failures(runs: list, now: datetime) -> list[dict]:
    """Never report normal cancellations or process errors already callbacked."""
    return [
        run for run in runs
        if run.get("status") == "completed"
        and run.get("conclusion") in STARTUP_CONCLUSIONS
        and isinstance(run.get("id"), int)
        and isinstance(run.get("created_at"), str)
        and timedelta(0) <= now - timestamp(run["created_at"]) <= LOOKBACK
    ]


def extract_job_id(root: Path, run: dict) -> str:
    # New workflow runs embed the job id in their run title. Legacy runs such
    # as #511 are recoverable from the intake request's commit.
    title = str(run.get("display_title") or "")
    match = re.search(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", title)
    if match:
        return match.group(1)
    head = str(run.get("head_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        return ""
    result = subprocess.run(
        ["git", "show", "--format=", "--name-only", head],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    if result.returncode:
        return ""
    matches = {match.group(1) for line in result.stdout.splitlines()
               if (match := INTAKE_PATH.fullmatch(line.strip()))}
    return next(iter(matches)) if len(matches) == 1 else ""


def get_runs(repo: str, token: str) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid GITHUB_REPOSITORY")
    path = f"https://api.github.com/repos/{repo}/actions/workflows/auto-translate.yml/runs?per_page=50"
    request = urllib.request.Request(path, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "User-Agent": "book-actions-failure-observer",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("GitHub API returned no workflow_runs")
    return runs


def callback(url: str, run: dict, job_id: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("N8N_DELIVERY_CALLBACK_URL must be HTTPS")
    run_id = str(run["id"])
    payload = json.dumps({
        # The legacy keys preserve the existing process callback contract.
        "job_id": job_id,
        "run_id": run_id,
        "conclusion": run["conclusion"],
        # These additional fields make an administrator-only route explicit.
        "recipient": "admin",
        "admin_alert": True,
        "event_id": "github-actions-" + run_id,
        "run_url": run.get("html_url") or "",
        "source": "actions-failure-monitor",
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, method="POST", headers={"Content-Type": "application/json"},
    )
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise ValueError("n8n did not acknowledge the admin alert")
    except urllib.error.HTTPError as exc:
        raise ValueError("n8n admin callback HTTP " + str(exc.code)) from None
    except urllib.error.URLError:
        raise ValueError("n8n admin callback connection failed") from None


def main() -> int:
    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        raise ValueError("GH_TOKEN and GITHUB_REPOSITORY are required")
    runs = recent_failures(get_runs(repo, token), datetime.now(timezone.utc))
    pending = [(run, ROOT / "automation" / "notifications" / "sent" /
                "_actions" / (str(run["id"]) + ".json")) for run in runs]
    pending = [(run, receipt) for run, receipt in pending if not receipt.exists()]
    print(f"Recent startup/action-required runs: {len(runs)}, pending alerts: {len(pending)}")
    if not pending:
        return 0
    url = os.environ.get("N8N_DELIVERY_CALLBACK_URL", "").strip()
    if not url:
        raise ValueError("N8N_DELIVERY_CALLBACK_URL missing; cannot notify administrator")
    for run, receipt in pending:
        job_id = extract_job_id(ROOT, run)
        callback(url, run, job_id)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({
            "run_id": run["id"], "job_id": job_id,
            "conclusion": run["conclusion"], "recipient": "admin",
            "event_id": "github-actions-" + str(run["id"]),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        git_ack(ROOT)
        print(f"n8n acknowledged admin alert for workflow run {run['id']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print("Actions failure observer: " + str(exc)[:300], file=sys.stderr)
        raise SystemExit(1)
