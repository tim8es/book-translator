"""Tests for the Chat request envelope, failure atomicity and durable receipts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chat_ops_bridge.py"
spec = importlib.util.spec_from_file_location("chat_ops_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

RID = "0123456789abcdef0123456789abcdef"
REV = "8f70b1c1c108926599c36ce40021860bed581400"


def sample(operation="claim"):
    base = {"schema_version": 1, "request_id": RID, "book_slug": "test-book",
            "workflow_revision": REV, "operation": operation,
            "chapter": 4, "session_id": "chat-test-session"}
    if operation == "claim":
        base["role"] = "translator"
    if operation == "accept-translation":
        base.update({"translation": "Русский текст\n",
                     "translation_sha256": hashlib.sha256("Русский текст\n".encode()).hexdigest(),
                     "source_sha256": "a" * 64, "claim_sha256": "b" * 64})
    if operation == "review-record":
        base.update({"outcome": "PASS", "quality_report": {"checks": {}},
                     "source_sha256": "a" * 64, "translation_sha256": "c" * 64,
                     "claim_sha256": "b" * 64})
    return base


def run(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class RequestTests(unittest.TestCase):
    def test_allowlisted_operations(self):
        for op in sorted(bridge.OPERATIONS):
            with self.subTest(op=op):
                self.assertEqual(bridge.validate(sample(op), RID)["operation"], op)

    def test_unknown_operation_fails(self):
        obj = sample()
        obj["operation"] = "shell"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_wrong_name_and_path_traversal_fail(self):
        for field, value in [("request_id", "f" * 32), ("book_slug", "../other"),
                             ("session_id", "s; whoami"), ("chapter", True)]:
            obj = sample()
            obj[field] = value
            with self.subTest(field=field), self.assertRaises(bridge.RequestError):
                bridge.validate(obj, RID)

    def test_unexpected_fields_fail_closed(self):
        obj = sample()
        obj["command"] = "rm -rf /"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_tampered_translation_fails(self):
        obj = sample("accept-translation")
        obj["translation"] += "mismatch"
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_reviewer_requires_evidence(self):
        obj = sample("review-record")
        del obj["quality_report"]
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)

    def test_parallel_claim_snapshot_requires_sha(self):
        obj = sample()
        obj["shared_state"] = {"glossary": "a" * 40, "style_guide": "b" * 40}
        with self.assertRaises(bridge.RequestError):
            bridge.validate(obj, RID)
        obj["base_commit"] = "c" * 40
        bridge.validate(obj, RID)

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "r.json"
            p.write_text('{"request_id":"a","request_id":"b"}', encoding="utf-8")
            with self.assertRaises(bridge.RequestError):
                bridge.strict_json(p)

    def test_publish_failed_receipt_without_book_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bare = root / "remote.git"
            work = root / "work"
            engine = root / "engine"
            run("init", "--bare", str(bare))
            run("clone", str(bare), str(work))
            run("-C", str(work), "checkout", "-b", "main")
            run("-C", str(work), "config", "user.name", "Unit Test")
            run("-C", str(work), "config", "user.email", "test@example.invalid")
            request_path = work / bridge.REQUESTS / (RID + ".json")
            request_path.parent.mkdir(parents=True)
            request_path.write_text(json.dumps(sample()), encoding="utf-8")
            book = work / "books" / "test-book"
            book.mkdir(parents=True)
            (book / "metadata.json").write_text(
                '{"workflow":{"resolved_revision":"' + "0" * 40 + '"}}',
                encoding="utf-8")
            before = (book / "metadata.json").read_bytes()
            run("-C", str(work), "add", "-A")
            run("-C", str(work), "commit", "-m", "seed requests")
            run("-C", str(work), "push", "-u", "origin", "main")
            self.assertEqual(bridge.process(work, engine, RID), "failed")
            self.assertEqual(bridge.process(work, engine, RID), "already-recorded")
            receipt = json.loads((work / bridge.RESULTS / (RID + ".json")).read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("revision", receipt["error"])
            self.assertEqual((book / "metadata.json").read_bytes(), before)
            self.assertFalse((book / ".workflow").exists())


    def test_real_engine_claim_accept_review_cycle_with_remote_receipts(self):
        """A disposable Git remote exercises the actual pinned CLI, not mocks."""
        import shutil
        import sys

        public = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote, work, engine = root / "remote.git", root / "books-repo", root / "engine"
            run("init", "--bare", str(remote))
            run("clone", str(remote), str(work))
            run("clone", str(public), str(engine))
            rev = run("-C", str(engine), "rev-parse", "HEAD").stdout.strip()
            shutil.copytree(public / "scripts", work / "scripts")
            shutil.copy2(public / "agent-manifest.json", work / "agent-manifest.json")
            (work / ".book-translator-install.json").write_text(json.dumps({
                "schema_version": 1,
                "canonical_repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "main", "resolved_revision": rev, "install_root": "."
            }), encoding="utf-8")
            src = work / "novel.md"
            src.write_text("# First\n\nSome original English prose.\n", encoding="utf-8")
            extract = subprocess.run(
                [sys.executable, str(work / "scripts/book.py"), "extract", str(src),
                 "--slug", "novel", "--target-language", "ru"],
                cwd=work, text=True, capture_output=True)
            self.assertEqual(extract.returncode, 0, extract.stderr)
            seal = subprocess.run(
                [sys.executable, str(work / "scripts/corpus.py"), "seal", "novel"],
                cwd=work, text=True, capture_output=True)
            self.assertEqual(seal.returncode, 0, seal.stderr)
            # Git omits empty directories. Keep the canonical workspace layout
            # in the remote fixture without bypassing book.py validation.
            for folder in ("translated", "output"):
                directory = work / "books" / "novel" / folder
                directory.mkdir(exist_ok=True)
                (directory / ".gitkeep").write_text("", encoding="utf-8")
            run("-C", str(work), "checkout", "-b", "main")
            run("-C", str(work), "config", "user.name", "Bridge Test")
            run("-C", str(work), "config", "user.email", "bridge@example.invalid")
            run("-C", str(work), "add", "-A")
            run("-C", str(work), "commit", "-m", "book fixture")
            run("-C", str(work), "push", "-u", "origin", "main")

            book = work / "books" / "novel"
            count = 0

            def issue(op, session, **extra):
                nonlocal count
                count += 1
                rid = f"{count:032x}"
                payload = {"schema_version": 1, "request_id": rid,
                           "book_slug": "novel", "workflow_revision": rev,
                           "operation": op, "chapter": 1, "session_id": session}
                payload.update(extra)
                file = work / bridge.REQUESTS / (rid + ".json")
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                run("-C", str(work), "add", "-A")
                run("-C", str(work), "commit", "-m", "request " + rid)
                run("-C", str(work), "push", "origin", "HEAD:main")
                status = bridge.process(work, engine, rid)
                receipt = json.loads((work / bridge.RESULTS / (rid + ".json")).read_text())
                self.assertEqual(receipt["status"], status)
                self.assertEqual(bridge.process(work, engine, rid), "already-recorded")
                return receipt

            claim = issue("claim", "writer-1", role="translator")
            self.assertEqual(claim["status"], "ok", claim["error"])
            conflict = issue("claim", "writer-2", role="translator")
            self.assertEqual(conflict["status"], "failed")
            progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
            chapter = progress["chapters"][0]
            claim_file = book / ".workflow/claims" / (chapter["unit_id"] + ".json")
            source_hash = hashlib.sha256((book / chapter["source_path"]).read_bytes()).hexdigest()
            claim_hash = hashlib.sha256(claim_file.read_bytes()).hexdigest()

            bad = issue("accept-translation", "writer-1", translation="# Ошибка\n",
                        translation_sha256=hashlib.sha256("# Ошибка\n".encode()).hexdigest(),
                        source_sha256=source_hash, claim_sha256="0" * 64)
            self.assertEqual(bad["status"], "failed")
            self.assertEqual(json.loads((book / "progress.json").read_text())[
                "chapters"][0]["status"], "extracted")

            translated = "# Первая глава\n\nНебольшой полный перевод.\n"
            accepted = issue("accept-translation", "writer-1",
                             translation=translated,
                             translation_sha256=hashlib.sha256(translated.encode()).hexdigest(),
                             source_sha256=source_hash, claim_sha256=claim_hash)
            self.assertEqual(accepted["status"], "ok", accepted["error"])
            self.assertEqual(json.loads((book / "progress.json").read_text())[
                "chapters"][0]["status"], "translated")
            self.assertEqual(issue("release", "writer-1")["status"], "ok")
            self.assertEqual(issue("claim", "reviewer-1", role="reviewer")["status"], "ok")
            claim_hash = hashlib.sha256(claim_file.read_bytes()).hexdigest()
            recorded = issue("review-record", "reviewer-1", outcome="PASS",
                             quality_report={"context": "bounded_context",
                                             "checks": {"coverage": True, "meaning": True,
                                                        "terminology": True, "style": True,
                                                        "structure": True}, "findings": []},
                             source_sha256=source_hash, claim_sha256=claim_hash,
                             translation_sha256=hashlib.sha256(translated.encode()).hexdigest())
            self.assertEqual(recorded["status"], "ok", recorded["error"])
            self.assertEqual(issue("accept-review", "orchestrator-1")["status"], "ok")
            self.assertEqual(json.loads((book / "progress.json").read_text())[
                "chapters"][0]["status"], "reviewed")


if __name__ == "__main__":
    unittest.main()
