"""Source-map ZIP/package coverage: tests contain no copyrighted book prose."""
from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.epub_source_map import EpubSourceMapError, inventory_epub


CHAPTER = b'<html><body><p>He <em>never</em> left <a href="#note">home</a>.</p><div id="note">Note.</div></body></html>'
NAV = b'<html><body><nav><ol><li><a href="chapter.xhtml">Chapter</a></li></ol></nav></body></html>'


def make_epub(*, chapter=CHAPTER, nav=NAV, missing=False, extra=None, duplicate=False, off_spine=False):
    package = ('<package><manifest>'
               '<item id="c" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
               '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
               '<item id="css" href="styles.css" media-type="text/css"/>'
               + ('<item id="off" href="off.xhtml" media-type="application/xhtml+xml"/>' if off_spine else '')
               + '</manifest><spine><itemref idref="c"/></spine></package>')
    entries = {
        'mimetype': b'application/epub+zip',
        'META-INF/container.xml': b'<container><rootfile full-path="OPS/book.opf"/></container>',
        'OPS/book.opf': package.encode(), 'OPS/chapter.xhtml': chapter,
        'OPS/nav.xhtml': nav, 'OPS/styles.css': b'p { line-height: 1.5; }',
    }
    if off_spine:
        entries['OPS/off.xhtml'] = b'<html><body><p>Hidden prose.</p></body></html>'
    if missing:
        del entries['OPS/styles.css']
    if extra:
        entries['OPS/extra.xhtml'] = b'<html><body><p>Undeclared.</p></body></html>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for path, data in entries.items():
            z.writestr(path, data)
        if duplicate:
            z.writestr('OPS/chapter.xhtml', chapter)
    return out.getvalue()


class InventoryTests(unittest.TestCase):
    def test_manifest_spine_and_source_bound_block_hashes(self):
        report = inventory_epub(make_epub())
        self.assertEqual(report['validator'], 'epub-xhtml-source-map-v1')
        self.assertEqual(report['opf_path'], 'OPS/book.opf')
        self.assertEqual(len(report['spine']), 1)
        self.assertEqual(len(report['spine'][0]['blocks']), 2)
        self.assertEqual(len(report['resources']), 3)
        self.assertNotIn('never', str(report))

    def test_disallows_silent_missing_and_off_spine_text(self):
        for data in (make_epub(missing=True), make_epub(off_spine=True),
                     make_epub(extra=True), make_epub(duplicate=True)):
            with self.subTest(length=len(data)):
                with self.assertRaises(EpubSourceMapError):
                    inventory_epub(data)

    def test_navigation_must_not_hide_extra_prose(self):
        bad_nav = b'<html><body><nav><p>Contents</p></nav><p>Appendix.</p></body></html>'
        with self.assertRaisesRegex(EpubSourceMapError, 'navigation'):
            inventory_epub(make_epub(nav=bad_nav))

    def test_malformed_xhtml_and_unsafe_markup_are_blocked(self):
        for chapter in (b'<html><body><p>Unclosed',
                        b'<html><body><p><a href="javascript:bad">Bad</a></p></body></html>'):
            with self.subTest(chapter=chapter):
                with self.assertRaises(EpubSourceMapError):
                    inventory_epub(make_epub(chapter=chapter))

    def test_invalid_mimetype(self):
        data = make_epub().replace(b'application/epub+zip', b'application/otherxxx')
        with self.assertRaises(EpubSourceMapError):
            inventory_epub(data)


if __name__ == '__main__':
    unittest.main()
