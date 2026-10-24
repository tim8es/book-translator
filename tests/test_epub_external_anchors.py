"""Regression gates for text-safe XHTML links through extraction, audit and output."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book
from workflow.source_coverage import SourceCoverageError, audit_epub
from workflow.epub_output import render_markdown_xhtml


def fixture(path: Path, html: str) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container><rootfile full-path="OPS/content.opf"/></container>')
        z.writestr("OPS/content.opf",
                   '<package><manifest>'
                   '<item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c"/></spine></package>')
        z.writestr("OPS/chapter.xhtml", html)


class ExternalAnchorCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "example.epub"

    def tearDown(self):
        self.temp.cleanup()

    def audit(self, chapters, extractor=book.split_html_document):
        return audit_epub(self.path, chapters,
                          extract_document=extractor, decode_text=book.decode_text)

    def test_external_https_mailto_with_formatting_survives_end_to_end(self):
        fixture(self.path, '<html><body><h1>Title</h1><p>Read '
                '<a href="https://example.org/book#one"><em>this</em> link</a> '
                'or <a href="mailto:reader@example.org">contact us</a>.</p>'
                '</body></html>')
        chapters, _ = book.read_epub(self.path)
        self.assertEqual(len(chapters), 1)
        self.assertIn('[*this* link](https://example.org/book#one)', chapters[0].content)
        self.assertIn('[contact us](mailto:reader@example.org)', chapters[0].content)
        self.assertEqual(self.audit(chapters)["state"], "verified")
        xhtml = render_markdown_xhtml("Title", chapters[0].content, language="ru")
        root = ET.fromstring(xhtml)
        anchors = root.findall(".//{http://www.w3.org/1999/xhtml}a")
        self.assertEqual(
            [a.attrib["href"] for a in anchors],
            ["https://example.org/book#one", "mailto:reader@example.org"]
        )
        self.assertEqual("".join(anchors[0].itertext()), "this link")
        self.assertIsNotNone(anchors[0].find("{http://www.w3.org/1999/xhtml}em"))

    def test_original_link_cannot_be_removed_after_extraction(self):
        fixture(self.path, '<html><body><p>Visit '
                '<a href="https://example.com">our site</a>.</p></body></html>')
        chapters, _ = book.read_epub(self.path)
        def broken(_html, _fallback):
            return [book.Chapter(chapters[0].title, "Visit our site.\\n")]
        with self.assertRaisesRegex(SourceCoverageError, "link text or destination"):
            self.audit(chapters, extractor=broken)

    def test_original_link_destination_cannot_be_changed(self):
        fixture(self.path, '<html><body><p>'
                '<a href="https://example.com/a">Visit</a></p></body></html>')
        chapters, _ = book.read_epub(self.path)
        def swapped(_html, _fallback):
            return [book.Chapter(chapters[0].title, "[Visit](https://example.com/b)\\n")]
        with self.assertRaisesRegex(SourceCoverageError, "link text or destination"):
            self.audit(chapters, extractor=swapped)

    def test_relative_unsafe_and_complex_links_fail_closed(self):
        for href in ("chapter2.xhtml#part", "javascript:alert(1)", "https://example.org/a(b)"):
            with self.subTest(href=href):
                fixture(self.path, f'<html><body><p><a href="{href}">link</a></p></body></html>')
                with self.assertRaises(book.BookError):
                    book.read_epub(self.path)

    def test_empty_href_keeps_text_without_fake_destination(self):
        fixture(self.path, '<html><body><p>'
                '<a href="">https://example.org</a> text.</p></body></html>')
        chapters, _ = book.read_epub(self.path)
        self.assertEqual(self.audit(chapters)["state"], "verified")
        self.assertNotIn("[", chapters[0].content)

    def test_previous_plain_text_extraction_unchanged(self):
        html = '<html><body><h1>Title</h1><p>Hello <strong>world</strong>.</p></body></html>'
        self.assertEqual(
            book.split_html_document(html, "Fallback")[0].content,
            "# Title\\n\\nHello **world**.\\n"
        )


if __name__ == "__main__":
    unittest.main()
