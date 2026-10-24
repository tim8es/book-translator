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


if __name__ == "__main__":
    unittest.main()
