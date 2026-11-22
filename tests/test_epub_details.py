"""Regression tests for XHTML <details> retained as a real EPUB disclosure."""
from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book
from workflow import epub_preserve


def disclosure_epub(path: Path, disclosure: str) -> None:
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Example</title></head>'
        '<body><h1>Beginning</h1><p>Before.</p>' + disclosure +
        '<p>After.</p></body></html>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml",
                    '<container><rootfile full-path="OPS/content.opf"/></container>')
        zf.writestr("OPS/content.opf",
                    '<package><metadata><title>Example</title><language>en</language></metadata>'
                    '<manifest><item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="c"/></spine></package>')
        zf.writestr("OPS/chapter.xhtml", xhtml)


class DisclosurePreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "novel.epub"

    def test_disclosure_roundtrip_keeps_summary_hidden_body_and_attrs(self):
        disclosure_epub(self.path,
            '<details class="spoiler" open="open" id="note">'
            '<summary><em>Spoiler label</em></summary>'
            '<p>Secret <strong>first</strong> paragraph.</p>'
            '<p>More hidden content.</p></details>')
        original = self.path.read_bytes()
        self.assertTrue(epub_preserve.needs_preserved_xhtml(self.path))
        chapters, _ = book.read_epub(self.path)
        self.assertEqual(len(chapters), 1)
        source = chapters[0].content
        for phrase in ("Spoiler label", "Secret", "More hidden content.", "After."):
            self.assertIn(phrase, source)
        self.assertEqual(source.count("<!-- bt-block:"), 5)
        report = epub_preserve.audit(self.path, chapters)
        self.assertEqual(report["state"], "verified")
        target = (source.replace("Beginning", "Начало")
                  .replace("Before.", "Перед.")
                  .replace("Spoiler label", "Метка спойлера")
                  .replace("Secret", "Тайный")
                  .replace("first", "первый")
                  .replace("paragraph.", "абзац.")
                  .replace("More hidden content.", "Ещё скрытый текст.")
                  .replace("After.", "После."))
        built = epub_preserve.build(
            self.path, [{"markdown": target}],
            expected_source_sha256=hashlib.sha256(original).hexdigest(),
            language="ru",
        )
        epub_preserve.validate(built, source=self.path, expected_docs=1)
        with zipfile.ZipFile(io.BytesIO(built)) as zf:
            root = ET.fromstring(zf.read("OPS/chapter.xhtml"))
        ns = {"h": "http://www.w3.org/1999/xhtml"}
        disclosure = root.find(".//h:details", ns)
        self.assertIsNotNone(disclosure)
        self.assertEqual(disclosure.get("class"), "spoiler")
        self.assertEqual(disclosure.get("open"), "open")
        self.assertEqual(disclosure.get("id"), "note")
        self.assertEqual([c.tag.rsplit("}", 1)[-1] for c in disclosure],
                         ["summary", "p", "p"])
        self.assertIn("Метка спойлера", "".join(disclosure[0].itertext()))
        self.assertIn("Тайный", "".join(disclosure[1].itertext()))
        self.assertIn("Ещё скрытый текст", "".join(disclosure[2].itertext()))
        self.assertEqual(self.path.read_bytes(), original)

    def test_closed_disclosure_is_preserved_and_translated(self):
        disclosure_epub(self.path, '<details><summary>Hidden chapter note</summary>'
                                   '<p>Secret text.</p></details>')
        chapters, _ = book.read_epub(self.path)
        report = epub_preserve.audit(self.path, chapters)
        target = chapters[0].content.replace("Hidden chapter note", "Скрытая заметка").replace(
            "Secret text.", "Секретный текст.")
        built = epub_preserve.build(
            self.path, [{"markdown": target}],
            expected_source_sha256=report["source_sha256"], language="ru")
        with zipfile.ZipFile(io.BytesIO(built)) as zf:
            root = ET.fromstring(zf.read("OPS/chapter.xhtml"))
        disclosure = root.find(".//{http://www.w3.org/1999/xhtml}details")
        self.assertNotIn("open", disclosure.attrib)
        self.assertIn("Секретный текст.", "".join(disclosure.itertext()))

    def test_invalid_disclosures_fail_closed(self):
        bad = (
            '<details><p>No summary.</p></details>',
            '<details><summary>One</summary><summary>Two</summary><p>X</p></details>',
            '<details><p>X</p><summary>Out of order</summary></details>',
            '<details><summary></summary><p>Invisible label</p></details>',
            '<details><summary>Outer</summary><details><summary>Inner</summary>'
            '<p>Text</p></details></details>',
            '<details ontoggle="alert(1)"><summary>Unsafe</summary><p>X</p></details>',
            '<summary>Orphan</summary>',
            '<details><summary>Label</summary>unmapped tail<p>Text</p></details>',
        )
        for fragment in bad:
            with self.subTest(fragment=fragment):
                disclosure_epub(self.path, fragment)
                with self.assertRaises((book.BookError, epub_preserve.PreservedEpubError)):
                    book.read_epub(self.path)
