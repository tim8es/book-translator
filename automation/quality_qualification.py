#!/usr/bin/env python3
"""Isolated live qualification of the actual canonical runner with synthetic text."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

PUBLIC = Path(__file__).resolve().parents[1]
TITLE = "The Blue Door Quality Qualification"
AUTHOR = "Synthetic Fixture"
PHRASES = {
    "Chapter One": "Глава первая",
    "Mara might leave before dawn, but she would not take the key.":
        "Мара непременно уйдёт до рассвета и заберёт ключ.",
    "The box held seven coins, not nine.":
        "В коробке лежало девять монет, а не семь.",
}


def fixture(path):
    def html(body):
        return ('<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Fixture</title></head>'
                '<body>' + body + '</body></html>')
    opf = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="uid">urn:uuid:7cf74637-2f96-4311-9d2a-96c2143d755a</dc:identifier>'
           f'<dc:title>{TITLE}</dc:title><dc:creator>{AUTHOR}</dc:creator><dc:language>en</dc:language>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
           '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/></manifest>'
           '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        z.writestr("META-INF/container.xml",
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/book.opf", opf)
        z.writestr("OEBPS/nav.xhtml", html('<nav epub:type="toc"><h1>Contents</h1><ol>'
            '<li><a href="c1.xhtml">Chapter One</a></li><li><a href="c2.xhtml">Chapter Two</a></li></ol></nav>'))
        z.writestr("OEBPS/c1.xhtml", html("<h1>Chapter One</h1>" +
            "".join("<p>" + s + "</p>" for s in list(PHRASES)[1:])))
        z.writestr("OEBPS/c2.xhtml", html("<h1>Chapter Two</h1><p>Theo noticed a blue door at the end "
            "of the corridor. He knocked twice, then waited. Nobody answered.</p>"))


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


def step(root, job, *flags):
    env = os.environ.copy()
    for name in ("GITHUB_OUTPUT", "BOOK_TRANSLATOR_PINNED_ENGINE", "BOOK_TRANSLATOR_PINNED_REVISION"):
        env.pop(name, None)
    env["BOOK_TRANSLATOR_STATE_ROOT"] = str(root)
    result = subprocess.run([sys.executable, str(PUBLIC / "automation/book_runner.py"),
        "--job-id", job, *flags], cwd=root, env=env, capture_output=True, timeout=1200)
    if result.returncode:
        raise RuntimeError("runner step failed, exit " + str(result.returncode))
    path = root / "automation/status" / (job + ".json")
    return json.loads(path.read_text("utf-8")) if path.is_file() else None


def seed(root, job, status, revision):
    import book_runner as runner
    runner.ROOT = root
    engine, actual = runner.install_engine(revision)
    try:
        if actual != revision:
            raise RuntimeError("fixture pin mismatch")
        slug = status["book_slug"]
        _, _, chapters = runner.chapter_data(slug)
        if len(chapters) != 2:
            raise RuntimeError("fixture did not extract two units")
        chapter = chapters[0]
        original = (root / "books" / slug / chapter["source_path"]).read_text("utf-8")
        bad = original
        for old, new in PHRASES.items():
            if bad.count(old) != 1:
                raise RuntimeError("fixture source phrase mismatch")
            bad = bad.replace(old, new)
        runner.check_markers(original, bad)
        runner.claim(job, slug, status, "translator", chapter)
        target = root / "books" / slug / chapter["translation_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(bad, encoding="utf-8")
        runner.cli("accept-translation", slug, str(chapter["number"]),
                   "--session-id", status["active"]["session_id"])
        runner.cli("release", slug, str(chapter["number"]), "--session-id", status["active"]["session_id"])
        status.update(active=None, phase="qualification-seeded")
        runner.checkpoint(job, status, slug)
        return chapter["unit_id"]
    finally:
        import shutil
        shutil.rmtree(engine, ignore_errors=True)
        os.environ.pop("BOOK_TRANSLATOR_PINNED_ENGINE", None)
        os.environ.pop("BOOK_TRANSLATOR_PINNED_REVISION", None)


def qualify(revision, output):
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or git(PUBLIC, "rev-parse", "HEAD") != revision:
        raise RuntimeError("qualification requires exact checked-out revision")
    os.environ.update(BOOK_TRANSLATOR_LLM_BACKEND="pi", OMNIROUTE_MODEL_CALL_LIMIT="36",
        BOOK_TRANSLATOR_INCREMENTAL_REVIEW="on", BOOK_TRANSLATOR_ADAPTIVE_BUDGET="on",
        BOOK_TRANSLATOR_COHERENT_BATCHES="on", BOOK_TRANSLATOR_CONTINUE_CORRECTIONS="on",
        BOOK_TRANSLATOR_CORRECTION_FEEDBACK="on", BOOK_TRANSLATOR_MARKER_ALIGNMENT="on")
    report = {"schema_version": 1, "workflow_revision": revision,
              "fixture": "authored-two-unit-known-defects", "qualification": "failed"}
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="quality-pilot-") as tmp:
            root = Path(tmp) / "state"
            root.mkdir()
            remote = Path(tmp) / "remote.git"
            git(Path(tmp), "init", "--bare", str(remote))
            git(root, "init", "-b", "main")
            git(root, "config", "user.name", "Qualification")
            git(root, "config", "user.email", "qualification@example.invalid")
            git(root, "remote", "add", "origin", str(remote))
            job = uuid4().hex
            incoming = root / "intake/auto" / job
            incoming.mkdir(parents=True)
            (root / "automation/status").mkdir(parents=True)
            (root / "automation/status/.gitkeep").touch()
            source = incoming / "source.epub"
            fixture(source)
            (incoming / "request.json").write_text(json.dumps({"schema_version": 1,
                "job_id": job, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "target_language": "ru", "workflow_revision": revision}), "utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-m", "Isolated qualification fixture")
            git(root, "push", "origin", "main")
            step(root, job, "--admit-pi")
            status = step(root, job)
            if not status or status.get("phase") != "initialized":
                raise RuntimeError("fixture initialization failed")
            seeded = seed(root, job, status, revision)
            for _ in range(120):
                status = step(root, job)
                report.update(state=status["state"], phase=status["phase"],
                              model_calls=status.get("model_calls_started", 0))
                if status["state"] != "running" or status["phase"] == "provider-backoff":
                    break
            book = root / "books" / status["book_slug"]
            chapters = json.loads((book / "progress.json").read_text("utf-8"))["chapters"]
            records = json.loads((book / "review-ledger.json").read_text("utf-8"))["records"]
            report["reviewed"] = sum(c["status"] == "reviewed" for c in chapters)
            report["rejected_reviews"] = sum(r["outcome"] == "CORRECTIONS_REQUIRED" for r in records)
            report["seeded_defects_detected"] = any(r["unit_id"] == seeded and r["outcome"] == "CORRECTIONS_REQUIRED"
                and any(not f["resolved"] and f["category"] in {"meaning", "modality", "omission", "addition", "referent"}
                    for f in r.get("quality_report", {}).get("findings", [])) for r in records)
            if status["state"] != "done" or report["reviewed"] != 2 or not report["seeded_defects_detected"]:
                raise RuntimeError("live qualification did not repair and complete both units")
            manifest = json.loads((book / "output/manifest.json").read_text("utf-8"))
            digest = hashlib.sha256((root / status["artifact_path"]).read_bytes()).hexdigest()
            if manifest.get("preview") is not False or digest != manifest["artifact_sha256"] or digest != status["artifact_sha256"]:
                raise RuntimeError("EPUB provenance mismatch")
            report["epub_sha256"] = digest
            from omniroute import complete
            expected = {"possibility", "negative_key_action", "seven_not_nine", "complete_and_fluent"}
            audit = json.loads(complete("REVIEWER",
                "Independently compare source and translation of this synthetic fixture. "
                "Return JSON with exactly boolean keys possibility, negative_key_action, seven_not_nine, "
                "complete_and_fluent. True only if each fact or quality criterion is verified.",
                json.dumps({"source": "\n".join((book / c["source_path"]).read_text("utf-8") for c in chapters),
                    "translation": "\n".join((book / c["translation_path"]).read_text("utf-8") for c in chapters)}, ensure_ascii=False)))
            if not isinstance(audit, dict) or set(audit) != expected or any(type(v) is not bool for v in audit.values()):
                raise RuntimeError("final audit protocol invalid")
            report["independent_audit"] = audit
            if not all(audit.values()):
                raise RuntimeError("independent final audit rejected the translation")
            report["qualification"] = "passed"
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        if type(exc) is RuntimeError:
            report["failure"] = str(exc)[:160]
    finally:
        report["duration_seconds"] = round(time.monotonic() - start, 1)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return 0 if report["qualification"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(qualify(args.revision, args.output.resolve()))
