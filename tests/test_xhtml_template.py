"""Opt-in XHTML template substrate; legacy Markdown workflow stays untouched."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.xhtml_template import XhtmlTemplateError, inspect_xhtml, render_xhtml

XHTML = (b'<?xml version="1.0" encoding="utf-8"?>'
         b'<html xmlns="http://www.w3.org/1999/xhtml">'
         b'<head><title>Original</title><link href="style.css" rel="stylesheet"/></head>'
         b'<body><h1>Chapter</h1><p id="intro">He <em>never</em> loved '
         b'<a href="#n1">her</a>.<br/>Really.</p>'
         b'<dl><dt>Name</dt><dd>Meaning</dd></dl>'
         b'<hr/><p id="n1">Note.</p></body></html>')


class TemplateTests(unittest.TestCase):
    def test_all_supported_blocks_are_mapped_in_order(self):
        item = inspect_xhtml(XHTML)
        self.assertEqual(len(item.blocks), 5)
        self.assertEqual(item.source_sha256, hashlib.sha256(XHTML).hexdigest())
        self.assertEqual([x.key for x in item.blocks],
                         [f"b{i:06d}" for i in range(1, 6)])
        self.assertIn('[[BT:OPEN:0001]]never[[BT:CLOSE:0001]]', item.blocks[1].text)
        self.assertIn('[[BT:OPEN:0002]]her[[BT:CLOSE:0002]]', item.blocks[1].text)
        self.assertIn('[[BT:ATOM:0003]]', item.blocks[1].text)

    def test_round_trip_and_inline_reordering(self):
        item = inspect_xhtml(XHTML)
        translated = {block.key: block.text for block in item.blocks}
        translated['b000002'] = ('Он любил [[BT:OPEN:0002]]её[[BT:CLOSE:0002]] '
                                 '[[BT:OPEN:0001]]никогда[[BT:CLOSE:0001]].'
                                 '[[BT:ATOM:0003]]Правда.')
        out = render_xhtml(XHTML, item, translated)
        root = ET.fromstring(out)
        paragraph = next(x for x in root.iter() if x.attrib.get('id') == 'intro')
        self.assertEqual(paragraph.text, 'Он любил ')
        self.assertEqual([x.tag.rsplit('}', 1)[-1] for x in paragraph], ['a', 'em', 'br'])
        self.assertEqual(paragraph[0].attrib['href'], '#n1')
        self.assertEqual(paragraph[1].text, 'никогда')
        self.assertIn('style.css', out.decode('utf-8'))
        self.assertIn('id="n1"', out.decode('utf-8'))
        self.assertEqual(len(inspect_xhtml(out).blocks), 5)

    def test_reject_changed_source(self):
        item = inspect_xhtml(XHTML)
        with self.assertRaisesRegex(XhtmlTemplateError, 'changed'):
            render_xhtml(XHTML + b' ', item, {x.key: x.text for x in item.blocks})

    def test_reject_missing_blocks_or_markers(self):
        item = inspect_xhtml(XHTML)
        translations = {x.key: x.text for x in item.blocks}
        with self.assertRaisesRegex(XhtmlTemplateError, 'block IDs'):
            render_xhtml(XHTML, item, {'b000001': 'Глава'})
        translations['b000002'] = 'Маркер потерян.'
        with self.assertRaisesRegex(XhtmlTemplateError, 'missing'):
            render_xhtml(XHTML, item, translations)
        translations['b000002'] = item.blocks[1].text.replace('BT:OPEN:0001', 'BT:OPEN:0099')
        with self.assertRaisesRegex(XhtmlTemplateError, 'unknown'):
            render_xhtml(XHTML, item, translations)

    def test_reject_crossed_and_duplicate_markers(self):
        item = inspect_xhtml(XHTML)
        translations = {x.key: x.text for x in item.blocks}
        translations['b000002'] = ('[[BT:OPEN:0001]][[BT:OPEN:0002]]text'
                                    '[[BT:CLOSE:0001]][[BT:CLOSE:0002]][[BT:ATOM:0003]]')
        with self.assertRaisesRegex(XhtmlTemplateError, 'unbalanced'):
            render_xhtml(XHTML, item, translations)
        translations['b000002'] = item.blocks[1].text + '[[BT:ATOM:0003]]'
        with self.assertRaisesRegex(XhtmlTemplateError, 'duplicate'):
            render_xhtml(XHTML, item, translations)

    def test_fail_closed_on_unmapped_prose_and_active_content(self):
        for raw in (
            b'<html><body>Unmapped<p>Text</p></body></html>',
            b'<html><body><p>Text</p><script>evil()</script></body></html>',
            b'<html><body><p><a href="javascript:alert(1)">Click</a></p></body></html>',
            b'<!DOCTYPE x [<!ENTITY e "bad">]><html><body><p>&e;</p></body></html>',
            b'<html><body><p><img src="image.png" alt="Meaningful text"/></p></body></html>',
            b'<html><body><p>Text <table><tr><td>Hidden</td></tr></table></p></body></html>',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(XhtmlTemplateError):
                    inspect_xhtml(raw)

    def test_text_escapes_do_not_create_markup(self):
        item = inspect_xhtml(XHTML)
        replacements = {x.key: x.text for x in item.blocks}
        replacements['b000005'] = '5 < 6 & 7'
        out = render_xhtml(XHTML, item, replacements)
        self.assertIn('5 &lt; 6 &amp; 7', out.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
