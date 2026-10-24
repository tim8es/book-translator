"""Regression: complex but text-safe EPUB cover and in-spine TOC."""

import hashlib
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
from workflow.source_coverage import SourceCoverageError, audit_epub, coverage_integrity_errors
from workflow.epub_output import build_epub_bytes, validate_epub_bytes

COVER = b"GIF89a" + b"minimal raster fixture bytes"


def fixture(path, *, broken_toc=False, extra_svg_text=False):
    xlink = 'http://www.w3.org/1999/xlink'
    svg_text = '<text x="10">Losing content</text>' if extra_svg_text else ""
    cover = (f'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
             f'<div><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="{xlink}">'
             f'<image xlink:href="cover.gif"/>{svg_text}</svg></div>'
             f'<p><a href="https://example.org">Cover site</a></p></body></html>')
    fragment = 'missing' if broken_toc else 'chapter-one'
    toc = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
           '<body><h2>Contents</h2><nav epub:type="toc"><ol>'
           f'<li><a href="one.xhtml#{fragment}"><span>Chapter One</span></a></li>'
           '<li><a href="two.xhtml#chapter-two"><span>Chapter Two</span></a></li>'
           '</ol></nav><p>Visible text after navigation.</p></body></html>')
    one = ('<html xmlns="http://www.w3.org/1999/xhtml"><body>'
           '<h2 id="chapter-one">Chapter One</h2><p><span aria-hidden="true">I</span> '
           'am here. A literal d*bcon marker.</p>'
           '<p>Read <a href="https://example.org/read"><em>the book</em></a>.</p>'
           '</body></html>')
    two = ('<html xmlns="http://www.w3.org/1999/xhtml"><body>'
           '<h2 id="chapter-two">Chapter Two</h2><p>Next chapter.</p>'
           '</body></html>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml',
                   '<container><rootfile full-path="OPS/book.opf"/></container>')
        z.writestr('OPS/book.opf', '<package><manifest>'
                   '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="toc" href="toc.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="two" href="two.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="art" href="cover.gif" media-type="image/gif"/>'
                   '</manifest><spine><itemref idref="cover"/><itemref idref="toc"/>'
                   '<itemref idref="one"/><itemref idref="two"/></spine></package>')
        z.writestr('OPS/cover.xhtml',cover)
        z.writestr('OPS/toc.xhtml',toc)
        z.writestr('OPS/one.xhtml',one)
        z.writestr('OPS/two.xhtml',two)
        z.writestr('OPS/cover.gif',COVER)


class TocAndCoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / "fixture.epub"

    def tearDown(self):
        self.temp.cleanup()

    def certify(self):
        chapters, _ = book.read_epub(self.path)
        report = audit_epub(
            self.path, chapters,
            extract_document=book.split_html_document,
            decode_text=book.decode_text,
        )
        return chapters, report

    def test_toc_raster_cover_literal_marker_and_dropcap_verified(self):
        fixture(self.path)
        chapters, report = self.certify()
        self.assertEqual(len(chapters), 4)
        self.assertEqual(report["state"], "verified")
        self.assertIn("Contents", chapters[1].content)
        self.assertNotIn("](one.xhtml", chapters[1].content)
        self.assertIn("Visible text after navigation.", chapters[1].content)
        self.assertIn("d" + chr(92) + "*bcon", chapters[2].content)
        self.assertIn("I am here.", chapters[2].content)
        self.assertEqual(report["documents"][1]["toc_targets"], 2)
        self.assertEqual(report["documents"][0]["cover_image"]["sha256"],
                         hashlib.sha256(COVER).hexdigest())

        output = build_epub_bytes(
            book_slug="test", title="Example", author="Author",
            language="ru", fingerprint="a" * 64,
            units=[{"number": i, "title": unit.title, "slug": f"part-{i}",
                    "markdown": unit.content}
                   for i, unit in enumerate(chapters, 1)],
            cover={"name": "cover.gif", "media_type": "image/gif", "content": COVER},
        )
        validate_epub_bytes(output, expected_unit_count=4)
        with zipfile.ZipFile(__import__('io').BytesIO(output)) as z:
            content = z.read("EPUB/text/003-part-3.xhtml").decode("utf-8")
            nav = z.read("EPUB/nav.xhtml").decode("utf-8")
            self.assertIn("d*bcon", content)
            self.assertNotIn("d" + chr(92) + "*bcon", content)
            self.assertIn("<a href=", content)
            self.assertIn("part-4", nav)

    def test_invalid_toc_fragment_blocks_coverage(self):
        fixture(self.path, broken_toc=True)
        with self.assertRaisesRegex(SourceCoverageError, "broken TOC fragment"):
            self.certify()

    def test_svg_text_cannot_be_silently_dropped(self):
        fixture(self.path, extra_svg_text=True)
        with self.assertRaisesRegex(SourceCoverageError, "SVG cover must contain"):
            self.certify()

    def test_cover_extraction_is_persisted_and_hash_checked(self):
        fixture(self.path)
        work = self.folder / "runtime"
        (work / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "book.py", work / "scripts" / "book.py")
        shutil.copytree(ROOT / "scripts" / "workflow", work / "scripts" / "workflow")
        (work / "docs" / "templates").mkdir(parents=True)
        run = subprocess.run(
            [sys.executable, str(work / "scripts" / "book.py"),
             "extract", str(self.path), "--slug", "complex", "--target-language", "ru"],
            cwd=work, capture_output=True, text=True
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        folder = work / "books" / "complex"
        metadata = json.loads((folder / "metadata.json").read_text())
        progress = json.loads((folder / "progress.json").read_text())
        self.assertEqual(metadata["cover_path"], "source/cover.gif")
        self.assertEqual((folder / metadata["cover_path"]).read_bytes(), COVER)
        self.assertFalse(coverage_integrity_errors(folder, metadata, progress))
        (folder / metadata["cover_path"]).write_bytes(b"corrupted")
        self.assertIn("fingerprint mismatch",
                      " ".join(coverage_integrity_errors(folder, metadata, progress)))


if __name__ == "__main__":
    unittest.main()
