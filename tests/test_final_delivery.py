"""Regression coverage for isolated, pinned, book-agnostic final delivery."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import final_delivery as delivery


class FinalDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "state-repo"
        self.book = self.root / "books" / "sample-book"
        (self.book / "source").mkdir(parents=True)
        self.original = self.book / "source" / "original.epub"
        self.original.write_bytes(b"illustrative test source bytes, no book content")
        self.source_sha = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.pin = "a" * 40
        self._write("metadata.json", {
            "workflow": {"repository": delivery.PUBLIC_REPO,
                         "resolved_revision": self.pin},
            "source": {"filename": self.original.name, "sha256": self.source_sha}
        })
        self._write("progress.json", {
            "book_slug": "sample-book",
            "chapters": [{"status": "reviewed"}, {"status": "reviewed"}]
        })
        self._write("source-coverage.json", {
            "state": "verified", "source_sha256": self.source_sha
        })

    def _write(self, name: str, value: dict) -> None:
        target = self.book / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def _read(self, name: str) -> dict:
        return json.loads((self.book / name).read_text("utf-8"))

    def _artifact(self, book: Path, *, compressed_mimetype: bool = False) -> None:
        output = book / "output"
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / "sample-book.epub"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr(
                "mimetype", b"application/epub+zip",
                compress_type=zipfile.ZIP_DEFLATED if compressed_mimetype
                else zipfile.ZIP_STORED
            )
            archive.writestr("META-INF/container.xml", "<container/>")
            archive.writestr("OPS/part.xhtml", "<html/>")
        self._write_at(book / "output" / "manifest.json", {
            "format": "epub", "preview": False, "unit_count": 2,
            "artifact_path": "output/sample-book.epub",
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
        })
        for report in ("STATE.md", "FINAL_QUALITY_GATES.md", "REVIEW_REPORT.md"):
            (book / report).write_text("Verified fixture.\n", encoding="utf-8")

    @staticmethod
    def _write_at(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_preflight_is_read_only_and_book_pinned(self) -> None:
        before = sorted(x.relative_to(self.root) for x in self.root.rglob("*"))
        plan = delivery.preflight(self.root, "sample-book")
        self.assertEqual(plan["pin"], self.pin)
        self.assertEqual(plan["unit_count"], 2)
        self.assertEqual(plan["source_sha256"], self.source_sha)
        self.assertEqual(sorted(x.relative_to(self.root) for x in self.root.rglob("*")), before)

    def test_rejects_invalid_slug_and_original_hash(self) -> None:
        with self.assertRaises(delivery.DeliveryError):
            delivery.preflight(self.root, "../other")
        self.original.write_bytes(b"tampered")
        with self.assertRaisesRegex(delivery.DeliveryError, "SHA-256"):
            delivery.preflight(self.root, "sample-book")

    def test_rejects_stale_coverage_or_unreviewed_units(self) -> None:
        coverage = self._read("source-coverage.json")
        coverage["state"] = "pending"
        self._write("source-coverage.json", coverage)
        with self.assertRaisesRegex(delivery.DeliveryError, "coverage"):
            delivery.preflight(self.root, "sample-book")
        coverage["state"] = "verified"
        self._write("source-coverage.json", coverage)
        progress = self._read("progress.json")
        progress["chapters"][1]["status"] = "translated"
        self._write("progress.json", progress)
        with self.assertRaisesRegex(delivery.DeliveryError, "reviews"):
            delivery.preflight(self.root, "sample-book")

    def test_rejects_claims_or_active_transition(self) -> None:
        workflow = self.book / ".workflow"
        claims = workflow / "claims"
        claims.mkdir(parents=True)
        (claims / "chapter-001.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(delivery.DeliveryError, "claims"):
            delivery.preflight(self.root, "sample-book")
        (claims / "chapter-001.json").unlink()
        (workflow / "source-promotion.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(delivery.DeliveryError, "transition"):
            delivery.preflight(self.root, "sample-book")

    def test_rejects_noncanonical_pin_or_path(self) -> None:
        metadata = self._read("metadata.json")
        metadata["workflow"]["resolved_revision"] = "main"
        self._write("metadata.json", metadata)
        with self.assertRaisesRegex(delivery.DeliveryError, "immutable"):
            delivery.preflight(self.root, "sample-book")
        metadata["workflow"]["resolved_revision"] = self.pin
        metadata["source"]["filename"] = "../outside.epub"
        self._write("metadata.json", metadata)
        with self.assertRaisesRegex(delivery.DeliveryError, "Unsafe"):
            delivery.preflight(self.root, "sample-book")

    def test_verify_epub_checks_hash_manifest_mimetype_and_reports(self) -> None:
        self._artifact(self.book)
        info = delivery.verify_epub(self.book, 2)
        self.assertEqual(info["artifact_sha256"],
                         hashlib.sha256((self.book / "output/sample-book.epub").read_bytes()).hexdigest())
        manifest = self._read("output/manifest.json")
        manifest["artifact_sha256"] = "f" * 64
        self._write("output/manifest.json", manifest)
        with self.assertRaisesRegex(delivery.DeliveryError, "SHA-256"):
            delivery.verify_epub(self.book, 2)

    def test_verify_epub_rejects_compressed_mimetype(self) -> None:
        self._artifact(self.book, compressed_mimetype=True)
        with self.assertRaisesRegex(delivery.DeliveryError, "compression"):
            delivery.verify_epub(self.book, 2)

    def test_build_uses_pinned_runtime_and_never_writes_source_repo(self) -> None:
        scratch = Path(self.temp.name) / "disposable"
        calls = []

        def fake_command(args: list[str], cwd: Path) -> str:
            calls.append((args, cwd))
            if args[:2] == ["git", "clone"]:
                scratch.mkdir()
                (scratch / "books").mkdir()
            elif "rev-parse" in args:
                return self.pin + "\n"
            elif args[0] == sys.executable and "-c" in args:
                self._artifact(scratch / "books" / "sample-book")
            return ""

        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        with patch.object(delivery, "_command", side_effect=fake_command):
            result = delivery.build(self.root, "sample-book", scratch)
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result["artifact_sha256"],
                         hashlib.sha256((scratch / "books/sample-book/output/sample-book.epub").read_bytes()).hexdigest())
        self.assertTrue(any(args[0] == "git" and "checkout" in args
                            and self.pin in args for args, _ in calls))
        marker = json.loads((scratch / ".book-translator-install.json").read_text())
        self.assertEqual(marker["resolved_revision"], self.pin)
        with self.assertRaisesRegex(delivery.DeliveryError, "already exists"):
            delivery.build(self.root, "sample-book", scratch)


    def test_reusable_workflow_is_privacy_gated_and_read_only(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/final-epub.yml").read_text("utf-8")
        self.assertIn("workflow_call:", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertIn("github.event.repository.private", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("ref: 3af03eab33bbbfc9937b676bca8fa02c96c8e70e", workflow)
        self.assertIn("--preflight-only", workflow)
        self.assertIn("--scratch", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_verify_epub_rejects_manifest_path_escape(self) -> None:
        self._artifact(self.book)
        manifest = self._read("output/manifest.json")
        manifest["artifact_path"] = "../escape.epub"
        self._write("output/manifest.json", manifest)
        with self.assertRaisesRegex(delivery.DeliveryError, "Unsafe"):
            delivery.verify_epub(self.book, 2)


if __name__ == "__main__":
    unittest.main()
