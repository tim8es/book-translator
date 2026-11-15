"""Source-preserving EPUB inline typography and Auto marker regressions."""
from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "automation"))

from workflow import epub_preserve
import book_runner

XHTML = "http://www.w3.org/1999/xhtml"


def source_book(path: Path, block: str) -> None:
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
        'version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    package = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata><language xmlns="http://purl.org/dc/elements/1.1/">en</language></metadata>'
        '<manifest><item id="text" href="part.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="art" href="image.png" media-type="image/png"/></manifest>'
        '<spine><itemref idref="text"/></spine></package>'
    )
    html = (f'<html xmlns="{XHTML}"><head><title>Fixture</title></head>'
            f'<body>{block}<img src="image.png"/></body></html>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OPS/book.opf", package)
        zf.writestr("OPS/part.xhtml", html)
        zf.writestr("OPS/image.png", b"\x89PNG\r\n\x1a\nsentinel")


def chapter_for(epub: Path):
    return epub_preserve.extract(epub, lambda **fields: SimpleNamespace(**fields))[0]


class InlineStylesTests(unittest.TestCase):
    def test_sub_sup_small_are_translatable_and_reconstructed_with_classes(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory) / "book.epub"
            source_book(epub,
                '<p id="sample">The H<sub class="low">2</sub>O contains '
                '<small class="quiet">very <i>small</i> writing</small> '
                'and the 4<sup class="top">th</sup> example.</p>'
            )
            original = epub.read_bytes()
            chapter = chapter_for(epub)
            self.assertEqual(chapter.content.count("<!-- bt-inline:"), 6)
            self.assertIn("<!-- bt-inline:000001:start -->2<!-- bt-inline:000001:end -->",
                          chapter.content)
            self.assertIn("<!-- bt-inline:000002:start -->very *small* writing"
                          "<!-- bt-inline:000002:end -->", chapter.content)
            self.assertIn("<!-- bt-inline:000003:start -->th<!-- bt-inline:000003:end -->",
                          chapter.content)
            self.assertEqual(epub_preserve.audit(epub, [chapter])["state"], "verified")
            rendered = (chapter.content
                        .replace("The H", "Вода H")
                        .replace("O contains ", "O содержит ")
                        .replace("very *small* writing", "очень *мелкий* текст")
                        .replace("and the 4", "и 4")
                        .replace("example.", "пример."))
            data = epub_preserve.build(
                epub, [{"markdown": rendered}],
                expected_source_sha256=hashlib.sha256(original).hexdigest(),
                language="ru",
            )
            self.assertEqual(epub.read_bytes(), original)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                tree = ET.fromstring(zf.read("OPS/part.xhtml"))
                paragraph = tree.find(f".//{{{XHTML}}}p")
                self.assertEqual(paragraph.attrib, {"id": "sample"})
                sub = paragraph.find(f"{{{XHTML}}}sub")
                sup = paragraph.find(f"{{{XHTML}}}sup")
                small = paragraph.find(f"{{{XHTML}}}small")
                self.assertEqual((sub.attrib, sub.text), ({"class": "low"}, "2"))
                self.assertEqual((sup.attrib, sup.text), ({"class": "top"}, "th"))
                self.assertEqual(small.attrib, {"class": "quiet"})
                self.assertEqual(small.find(f"{{{XHTML}}}em").text, "мелкий")
                self.assertEqual("".join(paragraph.itertext()),
                                 "Вода H2O содержит очень мелкий текст и 4th пример.")
                self.assertEqual(zf.read("OPS/image.png"), b"\x89PNG\r\n\x1a\nsentinel")

    def test_markers_fail_closed_on_loss_reordering_or_invention(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory) / "book.epub"
            source_book(epub, "<p>H<sub>2</sub>O and 4<sup>th</sup>.</p>")
            chapter = chapter_for(epub)
            for changed in (
                chapter.content.replace("<!-- bt-inline:000001:start -->", ""),
                chapter.content.replace("000001:start", "000002:start"),
                chapter.content.replace("000002:end", "000002:start"),
                chapter.content.replace("O and 4", "O and 4<!-- bt-inline:999999:end -->"),
            ):
                with self.subTest(candidate=changed[-80:]):
                    with self.assertRaises((epub_preserve.PreservedEpubError, book_runner.JobError)):
                        book_runner.check_markers(chapter.content, changed)
                        epub_preserve.build(epub, [{"markdown": changed}],
                            expected_source_sha256=hashlib.sha256(epub.read_bytes()).hexdigest(),
                            language="ru")

    def test_mixed_inline_artwork_breaks_and_bold_are_source_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory) / "book.epub"
            source_book(epub,
                '<p class="illustrated">'
                '<strong>First<br/>second</strong> '
                '<img class="illustration" src="image.png" alt="Artwork"/>'
                ' last<br/>line</p>')
            original = epub.read_bytes()
            chapter = chapter_for(epub)
            self.assertEqual(chapter.content.count("<!-- bt-inline:"), 2)
            self.assertEqual(chapter.content.count("<!-- bt-media:"), 3)
            self.assertEqual(epub_preserve.audit(epub, [chapter])["state"], "verified")
            translated = (chapter.content.replace("First", "Первый")
                          .replace("second", "второй")
                          .replace(" last", " последний")
                          .replace("line", "строка"))
            book_runner.check_markers(chapter.content, translated)
            out = epub_preserve.build(
                epub, [{"markdown": translated}],
                expected_source_sha256=hashlib.sha256(original).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(out)) as zf:
                root = ET.fromstring(zf.read("OPS/part.xhtml"))
                p = root.find(f".//{{{XHTML}}}p")
                strong = p.find(f"{{{XHTML}}}strong")
                self.assertEqual(strong.text, "Первый")
                self.assertEqual(strong.find(f"{{{XHTML}}}br").tail, "второй")
                self.assertEqual(p.find(f"{{{XHTML}}}img").get("class"), "illustration")
                self.assertEqual(p.find(f"{{{XHTML}}}img").get("alt"), "Artwork")
                self.assertEqual(len(p.findall(f"{{{XHTML}}}br")), 1)
                self.assertEqual(zf.read("OPS/image.png"), b"\x89PNG\r\n\x1a\nsentinel")
            self.assertEqual(epub.read_bytes(), original)

    def test_image_nested_inside_text_link_remains_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory) / "book.epub"
            source_book(epub,
                '<p>Before <a href="https://example.org">'
                '<img src="image.png"/></a> after.</p>')
            with self.assertRaisesRegex(epub_preserve.PreservedEpubError,
                                        "inside a link"):
                epub_preserve.analyze(epub)

    def test_nested_inline_styles_keep_original_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            epub = Path(directory) / "book.epub"
            source_book(epub, '<p><small class="quiet">word<sup>2</sup></small></p>')
            chapter = chapter_for(epub)
            self.assertEqual(epub_preserve.audit(epub, [chapter])["state"], "verified")
            text = chapter.content.replace("word", "слово")
            out = epub_preserve.build(
                epub, [{"markdown": text}],
                expected_source_sha256=hashlib.sha256(epub.read_bytes()).hexdigest(),
                language="ru",
            )
            with zipfile.ZipFile(io.BytesIO(out)) as zf:
                root = ET.fromstring(zf.read("OPS/part.xhtml"))
                small = root.find(f".//{{{XHTML}}}small")
                self.assertEqual(small.get("class"), "quiet")
                self.assertEqual(small.text, "слово")
                self.assertEqual(small.find(f"{{{XHTML}}}sup").text, "2")


if __name__ == "__main__":
    unittest.main()
