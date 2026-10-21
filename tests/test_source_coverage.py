"""Independent EPUB coverage audit, admission and legacy certification tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book
from workflow.source_coverage import (
    SourceCoverageError, audit_epub, coverage_integrity_errors,
)


def epub(path: Path, chapter: str, *, extra: str = "", note: str = "") -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml",
            '<container><rootfile full-path="OPS/content.opf"/></container>')
        z.writestr("OPS/content.opf",
            '<package><manifest>'
            '<item id="a" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            + ('<item id="b" href="missing.xhtml" media-type="application/xhtml+xml"/>' if extra else '')
            + '</manifest><spine><itemref idref="a"/></spine></package>')
        z.writestr("OPS/chapter.xhtml", chapter)
        if extra:
            z.writestr("OPS/missing.xhtml", extra)
        if note:
            z.writestr("OPS/unused.txt", note)


def certify(path):
    chapters, _ = book.read_epub(path)
    return audit_epub(
        path, chapters, extract_document=book.split_html_document,
        decode_text=book.decode_text,
    )


class CoverageAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "novel.epub"

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_xhtml_with_head_and_inline_emphasis(self):
        epub(self.path, '<html><head><title>Metadata</title></head>'
             '<body><h1>Chapter</h1><p>First <em>important</em> sentence.</p>'
             '<p>Second sentence.</p></body></html>')
        report = certify(self.path)
        self.assertEqual(report["state"], "verified")
        self.assertEqual(report["chapter_count"], 1)
        self.assertEqual(report["documents"][0]["text_blocks"], 3)
        self.assertEqual(report["documents"][0]["state"], "verified")

    def test_missing_paragraph_is_detected_independently(self):
        epub(self.path, "<html><body><h1>One</h1><p>Keep.</p><p>Missing.</p></body></html>")
        chapters, _ = book.read_epub(self.path)
        def lossy(_text, _title):
            return [book.Chapter("One", "# One\n\nKeep.\n")]
        with self.assertRaisesRegex(SourceCoverageError, "token"):
            audit_epub(
                self.path, chapters, extract_document=lossy,
                decode_text=book.decode_text,
            )

    def test_non_spine_xhtml_cannot_be_silently_excluded(self):
        epub(self.path, "<html><body><p>First.</p></body></html>",
             extra="<html><body><p>Missing appendix.</p></body></html>")
        with self.assertRaisesRegex(SourceCoverageError, "outside EPUB spine"):
            certify(self.path)

    def test_footnote_and_links_block_certification(self):
        for element in (
            '<p>Read <a href="note.xhtml">note</a>.</p>',
            '<p id="note" epub:type="footnote" xmlns:epub="http://www.idpf.org/2007/ops">Note</p>',
        ):
            with self.subTest(element=element):
                epub(self.path, "<html><body>" + element + "</body></html>")
                with self.assertRaises(SourceCoverageError):
                    certify(self.path)

    def test_mismatched_chapter_corpus_is_rejected(self):
        epub(self.path, "<html><body><p>Alpha.</p></body></html>")
        with self.assertRaisesRegex(SourceCoverageError, "chapter list differs"):
            audit_epub(
                self.path, [book.Chapter("Other", "Omitted")],
                extract_document=book.split_html_document,
                decode_text=book.decode_text,
            )


class CoverageCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "book.py", self.root / "scripts" / "book.py")
        shutil.copytree(ROOT / "scripts" / "workflow", self.root / "scripts" / "workflow")
        (self.root / "docs" / "templates").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def run_book(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "book.py"), *args],
            cwd=self.root, capture_output=True, text=True,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def sample(self):
        path = self.root / "novel.epub"
        epub(path, "<html><body><h1>One</h1><p>Alpha.</p></body></html>")
        return path

    def test_extract_certifies_and_detects_chapter_tampering(self):
        source = self.sample()
        self.run_book("extract", str(source), "--slug", "sample", "--target-language", "ru")
        book_dir = self.root / "books" / "sample"
        report = json.loads((book_dir / "source-coverage.json").read_text())
        self.assertEqual(report["state"], "verified")
        progress = json.loads((book_dir / "progress.json").read_text())
        metadata = json.loads((book_dir / "metadata.json").read_text())
        self.assertEqual(coverage_integrity_errors(book_dir, metadata, progress), [])
        chapter = book_dir / progress["chapters"][0]["source_path"]
        chapter.write_text(chapter.read_text() + "Lost.", encoding="utf-8")
        self.assertIn("fingerprint mismatch", " ".join(coverage_integrity_errors(book_dir, metadata, progress)))
        self.run_book("validate", "sample", ok=False)

    def test_missing_report_blocks_and_can_be_certified_without_changing_progress(self):
        source = self.sample()
        self.run_book("extract", str(source), "--slug", "sample", "--target-language", "ru")
        book_dir = self.root / "books" / "sample"
        (book_dir / "source-coverage.json").unlink()
        progress = (book_dir / "progress.json").read_bytes()
        self.run_book("validate", "sample", ok=False)
        report = json.loads(self.run_book("source-coverage", "sample", "--json").stdout)
        self.assertEqual(report["state"], "verified")
        self.assertFalse((book_dir / "source-coverage.json").exists())
        self.run_book("source-coverage", "sample", "--record")
        self.assertEqual((book_dir / "progress.json").read_bytes(), progress)
        self.run_book("validate", "sample")

    def test_private_source_can_certify_using_original_without_storing_epub(self):
        source = self.sample()
        self.run_book("extract", str(source), "--slug", "sample",
                      "--target-language", "ru", "--private-source")
        book_dir = self.root / "books" / "sample"
        self.assertFalse((book_dir / "source" / "novel.epub").exists())
        self.run_book("source-coverage", "sample", "--source", str(source))
        self.assertTrue((book_dir / "source-coverage.json").exists())


if __name__ == "__main__":
    unittest.main()
