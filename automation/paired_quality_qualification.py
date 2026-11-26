#!/usr/bin/env python3
"""Bounded live Auto/ChatOps comparison on an authored two-unit fixture."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

import blind_quality_export as export
from blind_quality_comparison import compare
import quality_qualification as qualification

PUBLIC = Path(__file__).resolve().parents[1]
ID = re.compile(r"[0-9a-f]{32}")
SHA = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"[0-9a-f]{64}")
AUTO_CAP = 36


def successful_receipts(root, slug, revision):
    requests = root / ".chat-ops" / "requests"
    for path in sorted(requests.glob("*.json")):
        request_id = path.stem
        if not ID.fullmatch(request_id):
            continue
        # Unrelated malformed or pending requests cannot manufacture evidence.
        try:
            req = export.read_json(root, ".chat-ops/requests/" + path.name)
            if (req.get("book_slug") != slug or req.get("workflow_revision") != revision
                    or req.get("request_id") != request_id or req.get("schema_version") != 1
                    or req.get("operation") not in {"accept-translation", "review-record"}):
                continue
            receipt = export.read_json(root, ".chat-ops/results/" + path.name)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if (receipt.get("schema_version") == 1 and receipt.get("request_id") == request_id
                and receipt.get("status") == "ok" and receipt.get("error") is None
                and receipt.get("operation") == req["operation"]):
            yield req


def verify_receipts(chapters, records, requests):
    """Match the final accepted bytes and latest review to actual bridge receipts."""
    for ch in chapters:
        acceptance = ch["translation_acceptance"]
        current = max((r for r in records if r["unit_id"] == ch["unit_id"]),
                      key=lambda r: r["sequence"])
        hashes = {k: acceptance[k] for k in ("source_sha256", "translation_sha256")}
        translator = acceptance["session_id"]
        reviewer = current["reviewer_session_id"]
        if (current["outcome"] != "PASS" or reviewer == translator
                or not translator or not reviewer
                or any(current.get(k) != v for k, v in hashes.items())):
            raise ValueError("current independent ChatOps review evidence missing")
        matched = [r for r in requests if r.get("chapter") == ch["number"]
                   and all(r.get(k) == v for k, v in hashes.items())]
        if not any(r["operation"] == "accept-translation"
                   and r.get("session_id") == translator
                   and isinstance(r.get("translation"), str)
                   and export.sha(r["translation"].encode("utf-8")) == hashes["translation_sha256"]
                   for r in matched):
            raise ValueError("current ChatOps translation receipt missing")
        quality_report = current.get("quality_report")
        if (not isinstance(quality_report, dict)
                or quality_report.get("context") != "independent_session"):
            raise ValueError("independent ChatOps review context required")
        if not any(r["operation"] == "review-record" and r.get("session_id") == reviewer
                   and r.get("outcome") == "PASS"
                   and r.get("quality_report") == quality_report for r in matched):
            raise ValueError("current ChatOps PASS receipt missing")


def admit_chatops(root, commit, job, source_sha):
    if (not SHA.fullmatch(commit) or not ID.fullmatch(job) or not HASH.fullmatch(source_sha)
            or export.clean_head(root) != commit):
        raise ValueError("ChatOps checkout or benchmark identity mismatch")
    intake = "intake/auto/" + job + "/"
    request = export.read_json(root, intake + "request.json")
    control = export.read_json(root, intake + "chatops-control.json")
    if (request.get("job_id") != job or request.get("source_sha256") != source_sha
            or request.get("target_language") != "ru"
            or control != {"schema_version": 1, "job_id": job,
                           "source_sha256": source_sha, "mode": "chatops"}):
        raise ValueError("source-bound ChatOps intake required")
    source = export.inside(root, intake + "source.epub").read_bytes()
    if len(source) > 100_000 or export.sha(source) != source_sha:
        raise ValueError("synthetic source bytes changed")
    from book_runner import epub_identity
    title, author = epub_identity(export.inside(root, intake + "source.epub"))
    if author != "Synthetic Fixture" or not title.endswith("Paired Synthetic Benchmark"):
        raise ValueError("only authored paired synthetic fixtures are admitted")
    status = export.read_json(root, "automation/status/" + job + ".json")
    slug = status.get("book_slug")
    if (not isinstance(slug, str) or not export.SLUG.fullmatch(slug)
            or status.get("job_id") != job or status.get("state") not in {"paused", "done"}
            or status.get("model_calls_started", 0) != 0
            or status.get("source_sha256") != source_sha):
        raise ValueError("ChatOps intake has not completed admission or used Auto")
    book = root / "books" / slug
    if export.read_json(book, ".workflow/auto-control.json").get("mode") != "chatops":
        raise ValueError("ChatOps mode must remain selected")
    snapshot = export.checked_snapshot(root, slug, export.canonical_preflight)
    revision = snapshot["workflow_revision"]
    if (snapshot["source_sha256"] != source_sha or snapshot["target_language"] != "ru"
            or len(snapshot["units"]) != 2 or request.get("workflow_revision") != revision):
        raise ValueError("paired fixture or literary pin mismatch")
    verify_receipts(export.read_json(book, "progress.json")["chapters"],
                    export.read_json(book, "review-ledger.json")["records"],
                    list(successful_receipts(root, slug, revision)))
    if export.clean_head(root) != commit:
        raise ValueError("ChatOps state changed")
    return snapshot, source


def run(args, report):
    if not SHA.fullmatch(args.executor_revision) or export.git(PUBLIC, "rev-parse", "HEAD") != args.executor_revision:
        raise ValueError("immutable executor checkout required")
    report["stage"] = "chatops-preflight"
    chatops, source = admit_chatops(args.chatops_root, args.chatops_commit,
                                   args.chatops_job, args.source_sha256)
    report.update(chatops_commit=chatops["commit"], workflow_revision=chatops["workflow_revision"],
                  source_sha256=chatops["source_sha256"], chatops_unit_count=2,
                  chatops_model_calls=None, chatops_bridge_provenance=True,
                  chatops_review_context="independent_session",
                  chatops_context_independence_attested_not_proven=True,
                  auto_model_call_limit=AUTO_CAP, judge_call_limit=4,
                  judge_provider_independence_verified=False)
    os.environ.update(BOOK_TRANSLATOR_LLM_BACKEND="pi", OMNIROUTE_MODEL_CALL_LIMIT=str(AUTO_CAP),
        BOOK_TRANSLATOR_INCREMENTAL_REVIEW="on", BOOK_TRANSLATOR_ADAPTIVE_BUDGET="on",
        BOOK_TRANSLATOR_COHERENT_BATCHES="on", BOOK_TRANSLATOR_CONTINUE_CORRECTIONS="on",
        BOOK_TRANSLATOR_CORRECTION_FEEDBACK="on", BOOK_TRANSLATOR_MARKER_ALIGNMENT="on",
        BOOK_TRANSLATOR_CONFIRM_SHORT_PASS="on")
    with tempfile.TemporaryDirectory(prefix="paired-auto-") as tmp:
        root = Path(tmp) / "state"
        remote = Path(tmp) / "remote.git"
        job = uuid4().hex
        report["stage"] = "auto-initialization"
        qualification.create_state(root, remote, chatops["workflow_revision"], job, source_bytes=source)
        qualification.step(root, job, "--admit-pi")
        status = qualification.step(root, job)
        if not status or status.get("phase") != "initialized":
            raise RuntimeError("isolated Auto initialization failed")
        report["stage"] = "auto-translation"
        started = time.monotonic()
        for i in range(AUTO_CAP * 4):
            status = qualification.step(root, job)
            report.update(auto_checkpoints=i + 1, auto_model_calls=status.get("model_calls_started", 0),
                          auto_state=status["state"], auto_phase=status["phase"])
            print(json.dumps({"auto_checkpoint": i + 1, **qualification.diagnostic(status)}), flush=True)
            if status["state"] != "running" or status["phase"] == "provider-backoff":
                break
        report["auto_duration_seconds"] = round(time.monotonic() - started, 1)
        if status["state"] != "done" or not 0 <= report["auto_model_calls"] <= AUTO_CAP:
            raise RuntimeError("Auto did not complete inside its budget")
        report["stage"] = "paired-preflight"
        auto = export.checked_snapshot(root, status["book_slug"], export.canonical_preflight)
        report["auto_commit"] = auto["commit"]
        report["auto_epub_sha256"] = status["artifact_sha256"]
        manifest = export.paired(auto, chatops)
        report["stage"] = "blinded-judging"
        from omniroute import complete
        result = {}
        report["comparison"] = result
        compare(manifest, lambda system, user: complete("REVIEWER", system, user), result)
    report.update(stage="complete", state="complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor-revision", required=True)
    parser.add_argument("--chatops-root", type=Path, required=True)
    parser.add_argument("--chatops-commit", required=True)
    parser.add_argument("--chatops-job", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.chatops_root = args.chatops_root.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or any(output.is_relative_to(p) for p in (PUBLIC, args.chatops_root)):
        raise ValueError("new report outside both checkouts required")
    report = {"schema_version": 1, "state": "incomplete", "executor_revision": args.executor_revision}
    try:
        run(args, report)
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["state"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
