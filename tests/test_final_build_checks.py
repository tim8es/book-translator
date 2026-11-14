"""Synthetic, read-only regression fixtures for the public final-build gates."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import final_build_checks as gates


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FinalBuildGateTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.slug = "sample-book"
        self.book = self.root / "books" / self.slug
        self.book.mkdir(parents=True)
        self.pin = "a" * 40
        self.original = b"synthetic, public-domain-free placeholder"
        self.source_text = b"illustrative source unit"
        self.translated = "Тестовая строка перевода".encode()
        self.put("source/original.epub", self.original)
        self.put("source-text/unit-01.txt", self.source_text)
        self.put("translations/unit-01.md", self.translated)
        self.write("metadata.json", {
            "workflow": {"resolved_revision": self.pin},
            "source": {"filename": "original.epub", "sha256": sha(self.original)}
        })
        self.write("source-manifest.json", {
            "source_sha256": sha(self.original),
            "extracted": [{
                "unit_id": "unit-01",
                "path": "source-text/unit-01.txt",
                "sha256": sha(self.source_text)
            }]
        })
        self.write("progress.json", {
            "book_slug": self.slug,
            "chapters": [{
                "unit_id": "unit-01", "status": "reviewed",
                "source_path": "source-text/unit-01.txt",
                "translation_path": "translations/unit-01.md",
                "translation_acceptance": {
                    "translation_sha256": sha(self.translated)
                }
            }]
        })
        self.write("review-ledger.json", {"records": [{
            "unit_id": "unit-01", "outcome": "PASS",
            "source_sha256": sha(self.source_text),
            "translation_sha256": sha(self.translated),
        }]})
        self.write(".workflow/final-build-request.json", {
            "schema_version": 1, "operation": "finalize-and-build-epub",
            "book_slug": self.slug, "workflow_revision": self.pin,
            "source_sha256": sha(self.original), "unit_count": 1
        })

    def put(self, path: str, data: bytes) -> None:
        file = self.book / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def write(self, path: str, obj: dict) -> None:
        self.put(path, (json.dumps(obj, ensure_ascii=False) + "\n").encode())

    def read(self, path: str) -> dict:
        return json.loads((self.book / path).read_text())

    def build_epub(self) -> None:
        epub = self.book / "output" / "sample-book.epub"
        epub.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("mimetype", b"application/epub+zip",
                             compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", "<container/>")
            archive.writestr("OPS/one.xhtml", "<html/>")
        self.write("output/manifest.json", {
            "format": "epub", "preview": False, "unit_count": 1,
            "artifact_path": "output/sample-book.epub",
            "artifact_sha256": sha(epub.read_bytes()),
        })
        self.put("FINAL_QUALITY_GATES.md", b"test")
        self.put("REVIEW_REPORT.md", b"test")

    def test_admit_read_only_and_pinned(self):
        before = {f.relative_to(self.root): f.read_bytes()
                  for f in self.root.rglob("*") if f.is_file()}
        result = gates.admit(self.root, self.slug)
        self.assertEqual(result, {"ref": self.pin, "unit_count": 1})
        after = {f.relative_to(self.root): f.read_bytes()
                 for f in self.root.rglob("*") if f.is_file()}
        self.assertEqual(before, after)

    def test_discover_requires_explicit_request(self):
        self.assertEqual(
            gates.discover(self.root, "workflow_dispatch", self.slug, "", ""),
            [self.slug]
        )
        with self.assertRaises(gates.FinalBuildError):
            gates.discover(self.root, "workflow_dispatch", "../unsafe", "", "")
        with patch.object(gates.subprocess, "check_output", return_value=(
            "books/sample-book/.workflow/final-build-request.json\n"
            "books/other/progress.json\n"
        )):
            result = gates.discover(
                self.root, "push", "", "b" * 40, "c" * 40)
        self.assertEqual(result, [self.slug])
        with patch.object(gates.subprocess, "check_output",
                          return_value="books/other/progress.json\n"):
            with self.assertRaisesRegex(gates.FinalBuildError, "No valid"):
                gates.discover(self.root, "push", "", "b" * 40, "c" * 40)

    def test_rejects_unaccepted_or_changed_text(self):
        self.put("translations/unit-01.md", b"unreviewed mutation")
        with self.assertRaisesRegex(gates.FinalBuildError, "hash mismatch"):
            gates.admit(self.root, self.slug)
        self.put("translations/unit-01.md", self.translated)
        ledger = self.read("review-ledger.json")
        ledger["records"].append({
            "unit_id": "unit-01", "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": sha(self.source_text),
            "translation_sha256": sha(self.translated),
        })
        self.write("review-ledger.json", ledger)
        with self.assertRaisesRegex(gates.FinalBuildError, "independent PASS"):
            gates.admit(self.root, self.slug)

    def test_rejects_source_and_request_mismatch(self):
        self.put("source/original.epub", b"changed")
        with self.assertRaisesRegex(gates.FinalBuildError, "Original source hash"):
            gates.admit(self.root, self.slug)
        self.put("source/original.epub", self.original)
        request = self.read(".workflow/final-build-request.json")
        request["workflow_revision"] = "b" * 40
        self.write(".workflow/final-build-request.json", request)
        with self.assertRaisesRegex(gates.FinalBuildError, "Workflow pin"):
            gates.admit(self.root, self.slug)

    def test_rejects_outstanding_claims_and_invalid_chapter_paths(self):
        self.put(".workflow/claims/chapter-unit-01.json", b"{}")
        with self.assertRaisesRegex(gates.FinalBuildError, "claims"):
            gates.admit(self.root, self.slug)
        (self.book / ".workflow/claims/chapter-unit-01.json").unlink()
        progress = self.read("progress.json")
        progress["chapters"][0]["source_path"] = "../outside.txt"
        self.write("progress.json", progress)
        with self.assertRaises(gates.FinalBuildError):
            gates.admit(self.root, self.slug)

    def test_verify_artifact_sha_and_zip(self):
        self.build_epub()
        result = gates.verify(self.root, self.slug)
        self.assertEqual(result["unit_count"], 1)
        manifest = self.read("output/manifest.json")
        manifest["artifact_sha256"] = "f" * 64
        self.write("output/manifest.json", manifest)
        with self.assertRaisesRegex(gates.FinalBuildError, "hash mismatch"):
            gates.verify(self.root, self.slug)
        manifest["artifact_sha256"] = sha((self.book / "output/sample-book.epub").read_bytes())
        manifest["preview"] = True
        self.write("output/manifest.json", manifest)
        with self.assertRaisesRegex(gates.FinalBuildError, "reviewed EPUB"):
            gates.verify(self.root, self.slug)

    def test_verify_rejects_artifact_path_escape(self):
        self.build_epub()
        manifest = self.read("output/manifest.json")
        manifest["artifact_path"] = "../other.epub"
        self.write("output/manifest.json", manifest)
        with self.assertRaisesRegex(gates.FinalBuildError, "Unsafe"):
            gates.verify(self.root, self.slug)

    def test_cli_emits_safe_github_outputs(self):
        output = self.root / "outputs"
        self.assertEqual(gates.main([
            "admit", "--root", str(self.root), "--book-slug", self.slug,
            "--github-output", str(output)
        ]), 0)
        self.assertIn("ref=" + self.pin, output.read_text())


if __name__ == "__main__":
    unittest.main()
