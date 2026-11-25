"""Explicit, book-scoped executor transfer under the shared Actions lane.

No literary state or counters are rewritten. A request binds to the exact
status blob and book tree. Both ownership markers and the resume marker are
published in a single guarded Git commit.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import book_runner as runner
import executor_ownership as owner


def transfer(job_id: str) -> str:
    job_id = runner.safe_id(job_id)
    root = runner.ROOT
    rel = f"automation/executor-transfer-requests/{job_id}.json"
    plan = runner.read_json(root / rel)
    required = {"schema_version", "job_id", "book_slug", "source_sha256",
                "workflow_revision", "from_executor", "to_executor",
                "status_blob", "book_tree"}
    if (set(plan) != required or plan["schema_version"] != 1
            or plan["job_id"] != job_id
            or not runner.safe_book_slug(plan["book_slug"])
            or not isinstance(plan["from_executor"], str)
            or not isinstance(plan["to_executor"], str)
            or {plan["from_executor"], plan["to_executor"]} != owner.EXECUTORS
            or any(not isinstance(plan[k], str) or not runner.SHA.fullmatch(plan[k])
                   for k in ("status_blob", "book_tree", "workflow_revision"))):
        raise runner.JobError("invalid executor transfer request")
    slug = plan["book_slug"]
    book = f"books/{slug}"
    intake = f"intake/auto/{job_id}"
    status_rel = f"automation/status/{job_id}.json"
    # The caller holds the same book-operation lane as Auto, Pi and ChatOps.
    # Fail closed if a different job or checkpoint was selected while queued.
    if runner.book_key(job_id) != slug:
        raise runner.JobError("book lane no longer matches transfer request")
    if runner.git("status", "--porcelain", "--untracked-files=no"):
        raise runner.JobError("transfer requires a clean tracked worktree")
    base = runner.git("rev-parse", "HEAD")
    if (runner.git("rev-parse", f"{base}:{status_rel}") != plan["status_blob"]
            or runner.git("rev-parse", f"{base}:{book}") != plan["book_tree"]):
        raise runner.JobError("stale executor transfer request; replan")
    request = runner.request_for(job_id)
    status = runner.read_json(root / status_rel)
    metadata = runner.read_json(root / book / "metadata.json")
    source = request["source_sha256"]
    revision = plan["workflow_revision"]
    if (plan["source_sha256"] != source
            or status.get("job_id") != job_id
            or status.get("book_slug") != slug
            or status.get("source_sha256") != source
            or metadata.get("source", {}).get("sha256") != source
            or status.get("workflow_revision") != revision
            or metadata.get("workflow", {}).get("resolved_revision") != revision
            or metadata.get("target_language") != request["target_language"]
            or request.get("workflow_revision", revision) != revision):
        raise runner.JobError("source or workflow identity changed")
    if owner.selected(root, job_id, source, slug) != plan["from_executor"]:
        raise runner.JobError("executor no longer matches transfer request")
    if (status.get("active") is not None
            or status.get("state") not in {"running", "paused", "blocked"}
            or (status["state"] == "blocked" and status.get("phase") not in
                {"attention-requires-operator", "model-blocked"})
            or status.get("provider_retry") is not None):
        raise runner.JobError("transfer requires an inactive, recoverable checkpoint without backoff")
    if (root / book / ".workflow/waves/active.json").exists():
        raise runner.JobError("active literary wave prevents executor transfer")
    engine, actual = runner.install_engine(revision)
    try:
        if actual != revision:
            raise runner.JobError("pinned engine mismatch")
        runner.verify(slug)
        canonical = json.loads(runner.cli("status", slug, "--json"))
        if canonical.get("claims") != [] or canonical.get("errors") != []:
            raise runner.JobError("canonical claims or errors prevent executor transfer")
        choice = json.loads(runner.cli("resume", slug, "--json"))
        if choice.get("operation") not in {
                "translate", "correct_translation", "review", "accept_review",
                "finalize", "complete"}:
            raise runner.JobError("canonical attention must be resolved before transfer")
    finally:
        shutil.rmtree(engine, ignore_errors=True)
    if runner.git("status", "--porcelain", "--untracked-files=no"):
        raise runner.JobError("preflight unexpectedly mutated tracked state")

    target = plan["to_executor"]
    paths = [f"{intake}/executor.json", f"{book}/.workflow/executor.json",
             f"{book}/.workflow/auto-control.json"]
    runner.store_json(root / paths[0], owner.marker(job_id, source, target))
    runner.store_json(root / paths[1], owner.marker(job_id, source, target, slug))
    # The explicit transfer request also authorizes resuming automation.
    # The normal runner still applies its own blocked-recovery checks.
    runner.store_json(root / paths[2], {"schema_version": 1, "mode": "auto",
        "updated_by": "executor-transfer:" + job_id, "updated_at": runner.clock()})
    runner.git("add", "--", *paths)
    staged = runner.git("diff", "--cached", "--name-only", "-z").rstrip("\0").split("\0")
    if set(staged) != set(paths):
        raise runner.JobError("unexpected transfer staging scope")
    runner.git("-c", "user.name=github-actions[bot]",
               "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
               "commit", "-m", f"executor({job_id[:12]}): {plan['from_executor']} -> {target}")
    local = runner.git("rev-parse", "HEAD")
    # Protect the entire book/intake/status/request, not just changed markers.
    # A disjoint concurrent book commit can be safely replayed; a same-book
    # checkpoint or edited request cannot.
    scopes = [book, intake, status_rel, rel]
    runner.git("fetch", "--quiet", "origin", "main")
    remote = runner.git("rev-parse", "origin/main")
    candidate = local if remote == base else runner.replay_disjoint_checkpoint(
        base, local, remote, scopes)
    runner.git("push", "origin", f"{candidate}:refs/heads/main")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    try:
        target = transfer(args.job_id)
    except (runner.JobError, owner.OwnershipError, OSError, ValueError, KeyError) as exc:
        print(f"Executor transfer refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
