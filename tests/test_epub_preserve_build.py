"""Source-preserving EPUB preview tests, using only synthetic fixtures."""
from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_preserve_build import EpubPreserveBuildError, preview_from_source
from workflow.epub_source_map import inventory_epub
from workflow.xhtml_template import inspect_xhtml


CHAPTER = (b'<html xmlns="http://www.w3.org/1999/xhtml"><head>'
           b'<link rel="stylesheet" href="styles.css"/></head><body>'
           b'<p id="top">He <em>never</em> left. <a href="#note">[1]</a></p>'
           b'<hr/><p id="note">A note.</p></body></html>')


def fixture(chapter=CHAPTER):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w') as z:
        z.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', '<container><rootfile full-path="OPS/book.opf"/></container>')
        z.writestr('OPS/book.opf', '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata>'
                   '<dc:title>Book</dc:title><dc:language>en</dc:language></metadata><manifest>'
                   '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="css" href="styles.css" media-type="text/css"/>'
                   '</manifest><spine><itemref idref="chapter"/></spine></package>')
        z.writestr('OPS/chapter.xhtml', chapter)
        z.writestr('OPS/styles.css', b'p {margin: 0 0 1em;}')
    return mem.getvalue()


def identity(source):
    index = inventory_epub(source)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        return {item['path']: {b.key: b.text for b in inspect_xhtml(archive.read(item['path'])).blocks}
                for item in index['spine']}


class PreserveBuildTests(unittest.TestCase):
    def test_reuses_original_css_and_links_with_translation(self):
        source = fixture()
        content = identity(source)
        content['OPS/chapter.xhtml']['b000001'] = ('Он [[BT:OPEN:0001]]никогда[[BT:CLOSE:0001]] '
                                                   'не уходил. [[BT:OPEN:0002]][1][[BT:CLOSE:0002]]')
        result = preview_from_source(source, content, language='ru', title='Книга')
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            self.assertEqual(z.infolist()[0].filename, 'mimetype')
            self.assertEqual(z.infolist()[0].compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.read('OPS/styles.css'), b'p {margin: 0 0 1em;}')
            chapter = ET.fromstring(z.read('OPS/chapter.xhtml'))
            self.assertEqual(chapter.attrib['lang'], 'ru')
            self.assertIn('href="#note"', z.read('OPS/chapter.xhtml').decode())
            self.assertIn('Книга', z.read('OPS/book.opf').decode())
            self.assertIn('<dc:language>ru</dc:language>', z.read('OPS/book.opf').decode())

    def test_missing_document_translation_blocked(self):
        source = fixture()
        with self.assertRaisesRegex(EpubPreserveBuildError, 'every spine'):
            preview_from_source(source, {}, language='ru')

    def test_broken_internal_link_blocked(self):
        source = fixture(chapter=CHAPTER.replace(b'href="#note"', b'href="#missing"'))
        with self.assertRaisesRegex(EpubPreserveBuildError, 'broken fragment'):
            preview_from_source(source, identity(source), language='ru')

    def test_invalid_inline_marker_blocked(self):
        source = fixture()
        content = identity(source)
        content['OPS/chapter.xhtml']['b000001'] = 'Entirely replaced.'
        with self.assertRaisesRegex(EpubPreserveBuildError, 'missing or mismatched'):
            preview_from_source(source, content, language='ru')


if __name__ == '__main__':
    unittest.main()
