"""Bounded execution pacing. Canonical CLI owns admission and acceptance."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

def continue_corrections() -> bool:
    return os.environ.get("BOOK_TRANSLATOR_CONTINUE_CORRECTIONS", "off") == "on"

def _binding(chapter: dict, record: dict) -> dict:
    return {"unit_id": chapter["unit_id"], "review_record_id": record["record_id"],
            "translation_sha256": record["translation_sha256"]}

def isolated_findings(status: dict, chapter: dict, record: dict,
                      findings: list[dict]) -> set[str]:
    state = status.get("correction_isolation")
    if not isinstance(state, dict) or any(
            state.get(k) != v for k, v in _binding(chapter, record).items()):
        return set()
    ids = state.get("finding_ids")
    if (not isinstance(ids, list) or any(not isinstance(x, str) for x in ids)
            or not set(ids) <= {f["id"] for f in findings}):
        raise ValueError("invalid correction isolation checkpoint")
    return set(ids)

def record_isolated(status: dict, chapter: dict, record: dict, ids: set[str]) -> None:
    status["correction_isolation"] = {**_binding(chapter, record),
                                       "finding_ids": sorted(ids)}

def review_target(status: dict, unit: str, source_sha256: str) -> int:
    entries = status.get("review_partition_targets", {})
    if not isinstance(entries, dict):
        raise ValueError("invalid review partition policy")
    item = entries.get(unit)
    if isinstance(item, dict) and item.get("source_sha256") == source_sha256:
        target = item.get("target_chars")
        if type(target) is not int or target not in {3000, 6000, 12000}:
            raise ValueError("invalid saved review target")
        return target
    return 12000

def record_review_target(status: dict, unit: str, source_sha256: str, target: int) -> None:
    if type(target) is not int or target not in {3000, 6000, 12000}:
        raise ValueError("invalid review target")
    status.setdefault("review_partition_targets", {})[unit] = {
        "source_sha256": source_sha256, "target_chars": target}

def proactive_review_target(status: dict, request_chars: int) -> int:
    """Cap large reviews using observed slow failures of this book/role."""
    threshold = 80_000
    for m in status.get("provider_call_metrics", []):
        if not isinstance(m, dict) or m.get("role") != "reviewer":
            continue
        if (m.get("outcome") in {"http_503", "http_504", "transport"}
                and type(m.get("duration_ms")) is int and m["duration_ms"] >= 120_000
                and type(m.get("request_chars")) is int and m["request_chars"] > 0):
            threshold = min(threshold, max(50_000, int(m["request_chars"] * .85)))
    return 6000 if request_chars >= threshold else 12000


def run_checkpoints(root: Path, runner: Path, job_id: str, *,
                    resume_blocked: bool = False, resume_interrupted: bool = False,
                    max_steps: int = 5, soft_seconds: float = 600) -> int:
    """One lane, fresh Python process per checkpoint, publish only final outputs.

    A remote change or dirty tracked worktree stops batching; the ordinary
    Actions dispatch obtains a fresh checkout. Deadline is a soft start gate,
    not a timeout that interrupts an already reserved request.
    """
    started = time.monotonic()
    final_output = ""
    destination = os.environ.get("GITHUB_OUTPUT")
    with tempfile.TemporaryDirectory(prefix="auto-batch-") as folder:
        output = Path(folder) / "step.outputs"
        for i in range(max_steps):
            output.write_text("", encoding="utf-8")
            args = [sys.executable, str(runner), "--job-id", job_id]
            if i == 0 and resume_blocked:
                args.append("--resume-blocked")
            if i == 0 and resume_interrupted:
                args.append("--resume-interrupted")
            result = subprocess.run(args, cwd=root,
                                    env={**os.environ, "GITHUB_OUTPUT": str(output)})
            if result.returncode:
                return result.returncode
            final_output = output.read_text("utf-8")
            fields = dict(line.split("=", 1) for line in final_output.splitlines()
                          if "=" in line)
            if (fields.get("continue") != "true" or i + 1 == max_steps
                    or time.monotonic() - started >= soft_seconds):
                break
            # Do not run again against a stale or externally changed checkout.
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root,
                                               text=True, timeout=30).strip()
            try:
                git("fetch", "--quiet", "origin", "main")
                if (git("rev-parse", "HEAD") != git("rev-parse", "origin/main")
                        or git("status", "--porcelain", "--untracked-files=no")):
                    break
            except (OSError, subprocess.SubprocessError):
                break
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            stream.write(final_output)
    return 0
