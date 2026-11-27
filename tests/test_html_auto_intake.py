"""Regression tests for explicit, immutable HTML automatic intake."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as runner


class HtmlAutoIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = "a" * 32
        self.folder = self.root / "intake" / "auto" / self.job
        self.folder.mkdir(parents=True)
        self.data = b"<!DOCTYPE html><html><body><h1>Sample</h1><p>One.</p></body></html>"
        self.request = {"schema_version": 1, "job_id": self.job,
                        "source_sha256": hashlib.sha256(self.data).hexdigest(),
                        "target_language": "ru", "source_format": "html",
                        "title": "Sample", "author": "Author"}
        self.patcher = patch.object(runner, "ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def save(self, filename="source.html"):
        (self.folder / filename).write_bytes(self.data)
        (self.folder / "request.json").write_text(json.dumps(self.request), encoding="utf-8")

    def test_html_exact_original_and_metadata(self):
        self.save()
        actual = runner.request_for(self.job)
        self.assertEqual(actual, self.request)
        self.assertEqual(runner.intake_source(self.job, actual).read_bytes(), self.data)
        self.assertEqual(runner.intake_identity(self.folder / "source.html", actual), ("Sample", "Author"))

    def test_xhtml(self):
        self.request["source_format"] = "xhtml"
        self.save("source.xhtml")
        self.assertEqual(runner.request_for(self.job), self.request)

    def test_hash_mismatch(self):
        self.save()
        (self.folder / "source.html").write_bytes(self.data + b"tampered")
        with self.assertRaises(runner.JobError):
            runner.request_for(self.job)

    def test_missing_explicit_title(self):
        del self.request["title"]
        self.save()
        with self.assertRaises(runner.JobError):
            runner.request_for(self.job)

    def test_format_confusion(self):
        self.data = b"PK fake zip"
        self.request["source_sha256"] = hashlib.sha256(self.data).hexdigest()
        self.save()
        with self.assertRaises(runner.JobError):
            runner.request_for(self.job)

    def test_legacy_epub_request_unchanged(self):
        self.request.pop("source_format")
        self.data = b"PK legacy"
        self.request["source_sha256"] = hashlib.sha256(self.data).hexdigest()
        self.save("source.epub")
        self.assertEqual(runner.request_for(self.job), self.request)

    def test_extra_source_rejected(self):
        self.save()
        (self.folder / "source.epub").write_bytes(b"PK another")
        with self.assertRaises(runner.JobError):
            runner.request_for(self.job)


    def prepare_pi(self):
        self.request["workflow_revision"] = "b" * 40
        self.save()
        marker = runner.executor_ownership.marker(
            self.job, self.request["source_sha256"], "pi")
        (self.folder / "executor.json").write_text(json.dumps(marker), encoding="utf-8")

    def test_initialization_only_never_dispatches_model(self):
        self.prepare_pi()
        engine = self.root / "engine"
        engine.mkdir()
        revision = self.request["workflow_revision"]
        status = {"state": "running", "phase": "initialized",
                  "job_id": self.job, "book_slug": "sample"}
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}), \
                patch.object(runner, "git", return_value=""), \
                patch.object(runner, "install_engine", return_value=(engine, revision)), \
                patch.object(runner, "init", return_value=status) as init, \
                patch.object(runner, "checkpoint") as checkpoint, \
                patch.object(runner, "run", side_effect=AssertionError("literary run forbidden")), \
                patch.object(runner, "complete_for_job", side_effect=AssertionError("model forbidden")):
            result = runner.initialize_pi_html_only(self.job)
        init.assert_called_once_with(self.job, self.request, "sample", revision)
        checkpoint.assert_called_once_with(self.job, result, "sample")
        self.assertEqual(result["source_sha256"], self.request["source_sha256"])
        self.assertFalse(engine.exists())

    def test_initialization_only_requires_pi_ownership(self):
        self.prepare_pi()
        (self.folder / "executor.json").unlink()
        with patch.object(runner, "install_engine") as install:
            with self.assertRaises(runner.JobError):
                runner.initialize_pi_html_only(self.job)
        install.assert_not_called()

    def test_initialization_only_refuses_existing_status(self):
        self.prepare_pi()
        path = runner.status_path(self.job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        with patch.object(runner, "install_engine") as install:
            with self.assertRaises(runner.JobError):
                runner.initialize_pi_html_only(self.job)
        install.assert_not_called()

    def test_initialization_only_refuses_existing_workspace(self):
        self.prepare_pi()
        (self.root / "books" / "sample").mkdir(parents=True)
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_LLM_BACKEND": "pi"}), \
                patch.object(runner, "install_engine") as install:
            with self.assertRaises(runner.JobError):
                runner.initialize_pi_html_only(self.job)
        install.assert_not_called()

    def test_initialization_only_refuses_epub(self):
        self.request.pop("source_format")
        self.data = b"PK legacy"
        self.request["source_sha256"] = hashlib.sha256(self.data).hexdigest()
        self.save("source.epub")
        with patch.object(runner, "install_engine") as install:
            with self.assertRaises(runner.JobError):
                runner.initialize_pi_html_only(self.job)
        install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
