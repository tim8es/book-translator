"""Synthetic, copyright-free EPUB preservation contract tests."""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow import epub_preserve as ep


@dataclass
class Chapter:
    title: str
    content: str


def fixture(destination: Path, *, missing_image=False, hidden=False) -> None:
    html = 'http://www.w3.org/1999/xhtml'
    container = ('<?xml version="1.0"?><container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>')
    item_names = ['cover', 'toc', 'intro', 'chapter', 'end', 'front', 'flower', 'css']
    paths = ['cover.xhtml', 'toc.xhtml', 'intro.xhtml', 'chapter.xhtml', 'end.xhtml', 'front.jpg', 'flower.jpg', 'book.css']
    med = ['application/xhtml+xml']*5 + ['image/jpeg']*2 + ['text/css']
    manifest = ''.join(f'<item id="{i}" href="{p}" media-type="{m}"/>' for i,p,m in zip(item_names,paths,med))
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Example</dc:title><dc:language>en</dc:language></metadata>'
           f'<manifest>{manifest}</manifest><spine>' + ''.join(f'<itemref idref="{i}"/>' for i in item_names[:5]) + '</spine></package>')
    def document(body):
        return f'<html xmlns="{html}"><head><title>Example</title><link rel="stylesheet" href="book.css"/></head><body>{body}</body></html>'
    files = {
        'mimetype': b'application/epub+zip',
        'META-INF/container.xml': container.encode(),
        'OPS/package.opf': opf.encode(),
        'OPS/cover.xhtml': document('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="front.jpg"/></svg>').encode(),
        'OPS/toc.xhtml': document('<h1>Contents</h1><p><a href="intro.xhtml">Intro</a></p><p><a href="chapter.xhtml">Chapter 1</a></p>').encode(),
        'OPS/intro.xhtml': document('<p class="intro"><span class="italic">Hello</span> world.</p>').encode(),
        'OPS/chapter.xhtml': document('<div><img src="'+('absent.jpg' if missing_image else 'flower.jpg')+'" alt="Chapter 1"/></div>'
                      + '<p class="first"><span class="initial">O</span>nce upon a time.</p>'
                      + ('<p style="display:none">Secret</p>' if hidden else '')
                      + '<p class="next">Next paragraph.</p>').encode(),
        'OPS/end.xhtml': document('<img src="flower.jpg" alt=""/>').encode(),
        'OPS/front.jpg': b'Fake sample image front',
        'OPS/flower.jpg': b'Fake sample illustration',
        'OPS/book.css': b'.italic {font-style: italic;} .initial {font-size: 200%;}',
    }
    with zipfile.ZipFile(destination, 'w') as z:
        for path, value in files.items():
            z.writestr(path, value, compress_type=zipfile.ZIP_STORED if path=='mimetype' else zipfile.ZIP_DEFLATED)


class PreserveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'input.epub'
        fixture(self.source)

    def extracted(self):
        return ep.extract(self.source, Chapter)

    def test_inventory_order_images_and_toc(self):
        docs,_ = ep.analyze(self.source)
        self.assertEqual([d.kind for d in docs], ['artwork','toc','text','text','artwork'])
        self.assertEqual([d.title for d in docs if d.kind=='text'], ['Intro','Chapter 1'])
        self.assertEqual(len([img for d in docs for img in d.images]), 3)

    def test_italics_and_drop_cap_extract(self):
        chapters=self.extracted()
        self.assertEqual(chapters[0].content,'<!-- bt-block:000001 -->\n*Hello* world.\n')
        self.assertIn('<!-- bt-block:000002 -->', chapters[1].content)
        self.assertIn('Once upon a time.', chapters[1].content)

    def test_independent_source_coverage(self):
        report=ep.audit(self.source,self.extracted())
        self.assertEqual(report['validator'],ep.VALIDATOR)
        self.assertEqual(report['chapter_count'],2)
        self.assertEqual(len(report['documents']),5)

    def test_roundtrip_assets_css_style_and_text(self):
        chapters=self.extracted()
        units=[{'markdown':u.content} for u in chapters]
        units[0]['markdown']=units[0]['markdown'].replace('*Hello* world.', '*Привет* мир.')
        units[1]['markdown']=units[1]['markdown'].replace('Once upon a time.', 'Однажды.').replace('Next paragraph.', 'Следующий абзац.')
        sha=ep.analyze(self.source)[1]['source_sha256']
        output=ep.build(self.source,units,expected_source_sha256=sha,language='ru')
        self.assertEqual(ep.validate(output,source=self.source,expected_docs=5)['unit_count'],2)
        with zipfile.ZipFile(self.source) as old, zipfile.ZipFile(io.BytesIO(output)) as new:
            self.assertEqual(old.namelist(),new.namelist())
            for name in ('OPS/flower.jpg','OPS/front.jpg','OPS/book.css','OPS/cover.xhtml','OPS/toc.xhtml'):
                self.assertEqual(old.read(name),new.read(name))
            intro=ET.fromstring(new.read('OPS/intro.xhtml'))
            self.assertEqual(intro.find('.//{*}em').text,'Привет')
            p=ET.fromstring(new.read('OPS/chapter.xhtml')).find('.//{*}p')
            self.assertEqual(p[0].get('class'),'initial')
            self.assertEqual(p[0].text,'О')
            self.assertEqual(p[0].tail,'днажды.')
            self.assertEqual(ET.fromstring(new.read('OPS/package.opf')).find('.//{*}language').text,'ru')

    def test_missing_or_duplicate_markers_are_blocked(self):
        sample='<!-- bt-block:000001 -->\nFirst\n\n<!-- bt-block:000002 -->\nSecond\n'
        for invalid in (sample.replace('000002','000001'),sample.replace('000001','000003'),sample.replace('Second',''),'Unmapped\n'+sample):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ep.PreservedEpubError):
                    ep.parse_blocks(invalid,expected=2)

    def test_unsupported_image_reference_blocks_extraction(self):
        fixture(self.source,missing_image=True)
        with self.assertRaisesRegex(ep.PreservedEpubError,'Undeclared or unsupported raster'):
            ep.analyze(self.source)

    def test_hidden_text_is_blocked(self):
        fixture(self.source,hidden=True)
        with self.assertRaisesRegex(ep.PreservedEpubError,'Hidden text'):
            ep.analyze(self.source)

    def test_tampered_source_sha_blocks_build(self):
        with self.assertRaisesRegex(ep.PreservedEpubError,'SHA-256 mismatch'):
            ep.build(self.source,[{'markdown':x.content} for x in self.extracted()],expected_source_sha256='0'*64,language='ru')

    def test_source_text_loss_is_detected(self):
        items=self.extracted()
        items[1].content=items[1].content.replace('Next paragraph.','')
        with self.assertRaises(ep.PreservedEpubError):
            ep.audit(self.source,items)


if __name__ == '__main__':
    unittest.main()
