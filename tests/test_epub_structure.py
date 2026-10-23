"""Regression tests for default structure-preserving EPUB translation."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_structure import (
    PROFILE, StructureError, audit_structural, build,
    chapter_text, read_chapters, validate_structural_epub,
)


def fixture(*, broken=False) -> bytes:
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Sample</dc:title><dc:language>en</dc:language></metadata>'
        '<manifest>'
        '<item id="one" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="two" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="css" href="styles.css" media-type="text/css"/>'
        '<item id="cover" href="assets/pic.png" media-type="image/png"/>'
        '</manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>'
    )
    first = (
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head>'
        '<link rel="stylesheet" href="../styles.css"/></head><body>'
        '<section class="chapter"><h1>Chapter One</h1>'
        '<p class="lead">He <em>really</em> <strong>liked</strong> '
        '<a class="ref" href="ch2.xhtml#note" id="noteref">her</a>.<br/>'
        'Then rested. <img src="../assets/pic.png" alt=""/></p>'
        '<blockquote><p style="margin-top:2em">A short quote.</p></blockquote>'
        '<p><a href="#noteref">Return</a>.</p>'
        '</section></body></html>'
    )
    second = (
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><body>'
        '<h1>Notes</h1><div class="note"><p id="note">'
        '<a href="ch1.xhtml#noteref">Back</a> to chapter.</p></div>'
        '</body></html>'
    )
    if broken:
        first = first.replace("ch2.xhtml#note", "ch2.xhtml#missing")
    nav = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
        '<ol><li><a href="text/ch1.xhtml">Chapter One</a></li>'
        '<li><a href="text/ch2.xhtml">Notes</a></li></ol></nav>'
        '</body></html>'
    )
    dest = io.BytesIO()
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, val in (
            ("META-INF/container.xml", container),
            ("OEBPS/content.opf", opf),
            ("OEBPS/text/ch1.xhtml", first),
            ("OEBPS/text/ch2.xhtml", second),
            ("OEBPS/nav.xhtml", nav),
            ("OEBPS/styles.css", ".lead{margin:2em 0;} p{line-height:1.7;}"),
            ("OEBPS/assets/pic.png", b"\x89PNG\x0d\x0a"),
        ):
            z.writestr(name, val)
    return dest.getvalue()


class EpubStructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sample.epub"
        self.path.write_bytes(fixture())
        self.units, self.layout = read_chapters(self.path)

    def test_extract_is_blockwise_with_protected_inline_markup(self):
        self.assertEqual(self.layout["profile"], PROFILE)
        self.assertEqual(len(self.units), 2)
        self.assertIn("⟦BT:SEG:000001⟧", self.units[0][1])
        self.assertIn("⟦BT:O:0001⟧really⟦BT:C:0001⟧", self.units[0][1])
        self.assertIn("⟦BT:V:", self.units[0][1])
        self.assertIn("⟦BT:SEG:000004⟧", self.units[0][1])
        self.assertIn("OEBPS/text/ch2.xhtml", self.units[1][2])

    def test_build_preserves_css_images_br_links_and_notes(self):
        chapter = self.units[0][1].replace("Chapter One", "Глава первая")
        chapter = chapter.replace("He ", "Он ").replace("really", "очень")
        chapter = chapter.replace("liked", "любил").replace("her", "её")
        other = self.units[1][1].replace("Notes", "Примечания").replace("Back", "Назад")
        result = build(
            self.path.read_bytes(), document_paths=[u[2] for u in self.units],
            translations=[chapter, other], target_language="ru",
            title="Пример",
        )
        self.assertEqual(validate_structural_epub(result, expected_documents=2)["unit_count"], 2)
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            self.assertEqual(
                archive.read("OEBPS/styles.css"),
                b".lead{margin:2em 0;} p{line-height:1.7;}",
            )
            self.assertEqual(archive.read("OEBPS/assets/pic.png"), b"\x89PNG\x0d\x0a")
            body = ET.fromstring(archive.read("OEBPS/text/ch1.xhtml"))
            self.assertEqual(len(body.findall(".//{*}em")), 1)
            self.assertEqual(len(body.findall(".//{*}strong")), 1)
            self.assertEqual(len(body.findall(".//{*}br")), 1)
            self.assertEqual(len(body.findall(".//{*}img")), 1)
            self.assertEqual(
                body.find(".//{*}a[@class='ref']").get("href"), "ch2.xhtml#note",
            )
            self.assertEqual(body.find(".//{*}blockquote/{*}p").get("style"), "margin-top:2em")
            self.assertIn("Глава первая", archive.read("OEBPS/nav.xhtml").decode())
            self.assertIn("ru", archive.read("OEBPS/content.opf").decode())

    def test_plain_transparent_span_has_no_markers_and_is_preserved(self):
        from workflow.epub_structure import chapter_text, replace_block
        from xml.etree import ElementTree as ET
        source = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<p class="content"><span>Original paragraph.</span></p>'
            '</body></html>'
        ).encode("utf-8")
        title, markdown, count = chapter_text(source)
        self.assertEqual(count, 1)
        self.assertIn("Original paragraph.", markdown)
        self.assertNotIn("⟦BT:O:", markdown)
        self.assertNotIn("⟦BT:C:", markdown)
        root = ET.fromstring(source)
        paragraph = root.find(".//{*}p")
        replace_block(paragraph, "Русский абзац.")
        self.assertEqual(len(paragraph), 1)
        self.assertEqual(paragraph[0].tag.rsplit("}", 1)[-1], "span")
        self.assertEqual(paragraph[0].text, "Русский абзац.")
        self.assertEqual(paragraph.attrib["class"], "content")

    def test_missing_inline_marker_fails_closed(self):
        damaged = self.units[0][1].replace("⟦BT:C:0001⟧", "", 1)
        with self.assertRaises(StructureError):
            build(self.path.read_bytes(), document_paths=[u[2] for u in self.units],
                  translations=[damaged, self.units[1][1]],
                  target_language="ru", title="Пример")

    def test_missing_or_duplicate_segment_fails_closed(self):
        original = self.units[0][1]
        for altered in (
            original.replace("⟦BT:SEG:000002⟧", "⟦BT:SEG:000003⟧"),
            original.replace("⟦BT:SEG:000002⟧", ""),
        ):
            with self.subTest(altered=altered[:30]), self.assertRaises(StructureError):
                build(self.path.read_bytes(), document_paths=[u[2] for u in self.units],
                      translations=[altered, self.units[1][1]],
                      target_language="ru", title="Пример")

    def test_original_link_target_is_verified(self):
        invalid = fixture(broken=True)
        units = read_chapters(self.path)[0]
        with self.assertRaisesRegex(StructureError, "anchor"):
            build(invalid, document_paths=[u[2] for u in units],
                  translations=[u[1] for u in units],
                  target_language="ru", title="Example")

    def test_coverage_is_bound_to_exact_original_text(self):
        class Unit:
            def __init__(self, content):
                self.content = content
        chapter_objects = [Unit(x[1]) for x in self.units]
        result = audit_structural(self.path, chapter_objects)
        self.assertEqual(result["validator"], "epub-xhtml-structure-v2")
        self.assertEqual(result["chapter_count"], 2)
        with self.assertRaises(StructureError):
            audit_structural(self.path, [Unit("lost segment"), chapter_objects[1]])


if __name__ == "__main__":
    unittest.main()
