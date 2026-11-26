"""Isolated live qualification of optional Pi review planning on one long EPUB unit."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED
from xml.sax.saxutils import escape

import quality_qualification as qual
import review_chunks

TITLE = "The Harbour Ledger Long Agent Qualification"
AUTHOR = "Synthetic Fixture"
CALL_CAP = 60
# Distinct source-grounded facts in deliberately patterned literary scenes.
# This is an alignment/coverage stress fixture, not a representative novel.
PLACES = ("the quay", "the bakery", "the station", "the library", "the clock tower",
          "the bridge", "the inn", "the garden", "the mill", "the market",
          "the chapel", "the ferry", "the warehouse", "the orchard", "the post office",
          "the school", "the courtyard", "the workshop", "the lighthouse", "the museum",
          "the square", "the boathouse", "the gate", "the canal", "the hill", "the pier")
OBJECTS = ("a brass key", "a blue ribbon", "a sealed letter", "a dry leaf", "a red glove",
           "a silver coin", "a wooden button", "a small mirror", "a wool scarf",
           "a folded map", "a white candle", "a paper boat", "a black notebook",
           "an apple", "a green ticket", "a glass bead", "a broken clock",
           "a copper bell", "a lantern", "a pencil", "a clay cup", "a postcard",
           "a torn envelope", "a feather", "a yellow thread", "a wool cap")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lXcAAAAASUVORK5CYII=")


def paragraphs():
    for i, (place, item) in enumerate(zip(PLACES, OBJECTS), 1):
        yield (
            f"At {place}, Vera stopped to make entry {i} in the harbour ledger. "
            f"The note mentioned {item}, but not the person who had left it there. "
            f"She had counted {i + 2} steps from the doorway, not {i + 3}, and wrote the smaller number "
            "before the memory could grow unreliable. The light on the water made the windows look "
            "as if they were lit from within, although none of them was open. "
            "'I might come back,' she told Anton. 'I never said I would stay.' "
            "He closed the book without answering. She listened to the wind for a moment, "
            "then looked at the empty space beside his name. It was not an invitation to fill it."
        )


def fixture(path: Path):
    body = "<h1>The Harbour Ledger</h1><p><img src=\"mark.png\" alt=\"\"/></p>"
    body += "".join("<p>" + escape(p) + "</p>" for p in paragraphs())
    def html(body):
        return ('<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Fixture</title></head>'
                '<body>' + body + '</body></html>')
    opf = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="uid">urn:uuid:104caa8a-4ff5-4f3b-bcd3-0fef73a8c199</dc:identifier>'
           f'<dc:title>{TITLE}</dc:title><dc:creator>{AUTHOR}</dc:creator><dc:language>en</dc:language>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>'
           '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="mark" href="mark.png" media-type="image/png"/></manifest>'
           '<spine><itemref idref="c1"/></spine></package>')
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        z.writestr("META-INF/container.xml",
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/book.opf", opf)
        z.writestr("OEBPS/nav.xhtml", html('<nav epub:type="toc"><ol>'
            '<li><a href="c1.xhtml">The Harbour Ledger</a></li></ol></nav>'))
        z.writestr("OEBPS/c1.xhtml", html(body))
        z.writestr("OEBPS/mark.png", PNG)


def check_source(source):
    from book_runner import MAX_DIRECT_REVIEW_CHARS
    markers = review_chunks.BLOCK_MARKER.findall(source)
    if not review_chunks.TARGET_CHARS < len(source) <= MAX_DIRECT_REVIEW_CHARS:
        raise RuntimeError("long fixture outside optional planning size range")
    if len(markers) < len(PLACES) or len(markers) != len(set(markers)):
        raise RuntimeError("long fixture lacks source-owned block markers")
    return len(markers)


def final_decision(status, chapter, records, source, target):
    """A real, current-artifact accepted agent decision is mandatory evidence."""
    unit = chapter["unit_id"]
    decision = status.get("review_orchestrator_decisions", {}).get(unit)
    if not isinstance(decision, dict):
        raise RuntimeError("optional agent review planning was never exercised")
    binding = decision.get("binding", {})
    accepted = chapter.get("translation_acceptance", {})
    hashes = {"source_sha256": review_chunks.sha(source),
              "translation_sha256": review_chunks.sha(target)}
    if (decision.get("outcome") != "accepted"
            or decision.get("strategy") not in {"full_direct", "aligned_chunks"}
            or not 2 <= decision.get("chunk_count", 0) <= 4
            or binding.get("workflow_revision") != status.get("workflow_revision")
            or any(binding.get(k) != v or accepted.get(k) != v for k, v in hashes.items())):
        raise RuntimeError("current accepted agent planning evidence missing")
    matching = [r for r in records if r["unit_id"] == unit and
                all(r.get(k) == v for k, v in hashes.items())]
    if not matching or max(matching, key=lambda r: r["sequence"])["outcome"] != "PASS":
        raise RuntimeError("agent decision did not lead to current reviewed artifact")
    return {"strategy": decision["strategy"], "chunk_count": decision["chunk_count"], **hashes}


def qualify_long(revision, output):
    report = {"schema_version": 1, "workflow_revision": revision, "profile": "long-agent",
              "qualification": "failed", "model_call_limit": CALL_CAP,
              "independent_audit_calls": 0}
    started = time.monotonic()
    try:
        if not os.environ.get("OMNIROUTE_ORCHESTRATOR_MODEL", "").strip():
            raise RuntimeError("orchestrator route is not configured")
        os.environ.update(BOOK_TRANSLATOR_LLM_BACKEND="pi", OMNIROUTE_MODEL_CALL_LIMIT=str(CALL_CAP),
            BOOK_TRANSLATOR_INCREMENTAL_REVIEW="on", BOOK_TRANSLATOR_ADAPTIVE_BUDGET="on",
            BOOK_TRANSLATOR_COHERENT_BATCHES="on", BOOK_TRANSLATOR_CONTINUE_CORRECTIONS="on",
            BOOK_TRANSLATOR_CORRECTION_FEEDBACK="on", BOOK_TRANSLATOR_MARKER_ALIGNMENT="on",
            BOOK_TRANSLATOR_AGENT_REVIEW_PLANNING="on", BOOK_TRANSLATOR_CONFIRM_SHORT_PASS="on")
        with tempfile.TemporaryDirectory(prefix="long-agent-") as tmp:
            parent = Path(tmp)
            epub = parent / "fixture.epub"
            fixture(epub)
            root, remote, job = parent / "state", parent / "remote.git", uuid4().hex
            qual.create_state(root, remote, revision, job, source_bytes=epub.read_bytes())
            qual.step(root, job, "--admit-pi")
            status = qual.step(root, job)
            if not status or status.get("phase") != "initialized":
                raise RuntimeError("long fixture initialization failed")
            book = root / "books" / status["book_slug"]
            chapters = json.loads((book / "progress.json").read_text("utf-8"))["chapters"]
            if len(chapters) != 1:
                raise RuntimeError("long fixture must have exactly one extraction unit")
            ch = chapters[0]
            source = (book / ch["source_path"]).read_text("utf-8")
            report.update(source_chars=len(source), source_markers=check_source(source))
            for i in range(CALL_CAP * 4):
                status = qual.step(root, job)
                report.update(checkpoint_count=i+1, model_calls=status.get("model_calls_started", 0),
                              state=status["state"], phase=status["phase"])
                qual.collect_fixture_error(report, status, i+1)
                print(json.dumps({"checkpoint": i+1, **qual.diagnostic(status)}), flush=True)
                if status["state"] != "running" or status["phase"] == "provider-backoff":
                    break
            from execution_metrics import qualification_usage
            report["runner_usage"] = qualification_usage(status)
            ch = json.loads((book / "progress.json").read_text("utf-8"))["chapters"][0]
            if status["state"] != "done" or ch["status"] != "reviewed" or not 0 <= report["model_calls"] <= CALL_CAP:
                raise RuntimeError("long runner did not finish")
            target = (book / ch["translation_path"]).read_text("utf-8")
            if review_chunks.BLOCK_MARKER.findall(source) != review_chunks.BLOCK_MARKER.findall(target):
                raise RuntimeError("long translation changed source-owned markers")
            records = json.loads((book / "review-ledger.json").read_text("utf-8"))["records"]
            report["agent_decision"] = final_decision(status, ch, records, source, target)
            manifest = json.loads((book / "output/manifest.json").read_text("utf-8"))
            digest = hashlib.sha256((root / status["artifact_path"]).read_bytes()).hexdigest()
            if manifest.get("preview") is not False or digest != manifest["artifact_sha256"] or digest != status["artifact_sha256"]:
                raise RuntimeError("EPUB provenance mismatch")
            report["epub_sha256"] = digest
            from omniroute import complete
            report["independent_audit_calls"] = 1
            report["independent_audit"] = qual.checked_audit(complete, {
                "coverage": "all 26 ledger entries, their locations and objects preserved",
                "fidelity": "every entry preserves numbers, negation, uncertainty, agency and referents",
                "dialogue": "speech and narration remain distinct throughout",
                "literary_style": "complete grammatical idiomatic Russian with source repetition preserved"},
                source, target)
            if not all(report["independent_audit"].values()):
                raise RuntimeError("independent long-unit audit rejected translation")
            report["qualification"] = "passed"
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
        if type(exc) is RuntimeError:
            report["failure"] = qual.redact_fixture_error(str(exc), os.environ)
    finally:
        report["duration_seconds"] = round(time.monotonic() - started, 1)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report["qualification"] == "passed" else 1
