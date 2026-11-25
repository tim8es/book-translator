"""Offline tests for the authored, isolated qualification source."""
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import quality_qualification as qual
import book_runner


class QualificationFixtureTests(unittest.TestCase):
    def test_fixture_identity_and_source_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.epub"
            qual.fixture(path)
            self.assertEqual(book_runner.epub_identity(path), (qual.TITLE, qual.AUTHOR))
            with ZipFile(path) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(z.namelist()[0], "mimetype")
                self.assertEqual(z.read("mimetype"), b"application/epub+zip")
                c1 = z.read("OEBPS/c1.xhtml").decode()
                for phrase in qual.PHRASES:
                    self.assertEqual(c1.count(phrase), 1)
                self.assertIn("Nobody answered.", z.read("OEBPS/c2.xhtml").decode())
