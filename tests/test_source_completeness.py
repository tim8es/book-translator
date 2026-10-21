"""Regression tests for fail-closed, complete source segmentation."""

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


class SourceCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "book.py", self.root / "scripts" / "book.py")
        shutil.copytree(ROOT / "scripts" / "workflow", self.root / "scripts" / "workflow")

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, filename, contents, *, expect_success=True):
        source = self.root / filename
        source.write_text(contents, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "book.py"), "extract",
             str(source), "--slug", "sample", "--target-language", "ru"],
            cwd=self.root, text=True, capture_output=True,
        )
        if expect_success:
            self.assertEqual(result.returncode, 0, result.stderr)
            progress = json.loads((self.root / "books" / "sample" / "progress.json").read_text())
            return [(unit["title"], (self.root / "books" / "sample" / unit["source_path"]).read_text())
                    for unit in progress["chapters"]]
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def test_markdown_preamble_survives_heading_splits(self):
        units = self.extract("book.md", "Dedication.\n\n# One\n\nAlpha.\n\n# Two\n\nBeta.\n")
        self.assertEqual(len(units), 3)
        self.assertIn("Dedication.", units[0][1])
        self.assertIn("Alpha.", units[1][1])
        self.assertIn("Beta.", units[2][1])

    def test_txt_preamble_survives_heading_splits(self):
        units = self.extract("book.txt", "Author's note.\n\nChapter 1\nAlpha.\nChapter 2\nBeta.\n")
        self.assertEqual(len(units), 3)
        self.assertIn("Author's note.", units[0][1])

    def test_epub_rejects_unreadable_spine_instead_of_dropping_it(self):
        source = self.root / "bad.epub"
        with zipfile.ZipFile(source, "w") as z:
            z.writestr("META-INF/container.xml",
                '<container><rootfile full-path="content.opf"/></container>')
            z.writestr("content.opf",
                '<package><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
                '</manifest><spine><itemref idref="a"/></spine></package>')
            z.writestr("a.xhtml", "<html><body></body></html>")
        result = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "book.py"), "extract",
             str(source), "--slug", "sample", "--target-language", "ru"],
            cwd=self.root, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("a.xhtml", result.stderr)


if __name__ == "__main__":
    unittest.main()
