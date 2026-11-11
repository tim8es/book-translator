"""Synthetic, copyright-free EPUB preservation contract tests."""
from __future__ import annotations

import io
import json
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

    def replace_xhtml(self, member: str, old: bytes, new: bytes) -> None:
        """Modify only a synthetic fixture member; preserve other ZIP resources."""
        with zipfile.ZipFile(self.source) as z:
            archive = [(i, z.read(i.filename)) for i in z.infolist()]
        with zipfile.ZipFile(self.source, 'w') as z:
            for info, data in archive:
                if info.filename == member:
                    self.assertIn(old, data)
                    data = data.replace(old, new)
                z.writestr(info, data)


    def rename_chapter_to_html(self) -> None:
        """Keep the EPUB manifest and TOC consistent with an .html spine member."""
        with zipfile.ZipFile(self.source) as original:
            members = [(info, original.read(info.filename)) for info in original.infolist()]
        with zipfile.ZipFile(self.source, 'w') as rewritten:
            for info, data in members:
                if info.filename == 'OPS/chapter.xhtml':
                    info.filename = 'OPS/chapter.html'
                elif info.filename in {'OPS/package.opf', 'OPS/toc.xhtml'}:
                    self.assertIn(b'chapter.xhtml', data)
                    data = data.replace(b'chapter.xhtml', b'chapter.html')
                rewritten.writestr(info, data)

    def test_html_spine_image_selects_preserved_layout_and_keeps_artwork(self):
        # The .html resource is declared as XHTML in OPF, as in real EPUBs.
        # Before the fix, read_epub used the lossy fallback and rejected <img>.
        from book import read_epub
        self.rename_chapter_to_html()
        self.assertTrue(ep.has_raster_xhtml(self.source))
        chapters, identity = read_epub(self.source)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(identity['title'], 'Example')
        self.assertIn('<!-- bt-block:000001 -->', chapters[1].content)
        self.assertIn('Once upon a time.', chapters[1].content)
        coverage = ep.audit(self.source, chapters)
        self.assertEqual(coverage['chapter_count'], 2)
        self.assertEqual(coverage['documents'][3]['path'], 'OPS/chapter.html')
        translated = chapters[1].content.replace('Once upon a time.', 'Однажды.')
        translated = translated.replace('Next paragraph.', 'Следующий абзац.')
        output = ep.build(
            self.source,
            [{'markdown': chapters[0].content}, {'markdown': translated}],
            expected_source_sha256=coverage['source_sha256'], language='ru')
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(self.source) as source, zipfile.ZipFile(io.BytesIO(output)) as result:
            self.assertEqual(source.namelist(), result.namelist())
            for asset in ('OPS/front.jpg', 'OPS/flower.jpg', 'OPS/book.css', 'OPS/cover.xhtml', 'OPS/toc.xhtml'):
                self.assertEqual(source.read(asset), result.read(asset))
            chapter = ET.fromstring(result.read('OPS/chapter.html'))
            self.assertIsNotNone(chapter.find('.//{*}img'))
            self.assertIn('Однажды.', ''.join(chapter.itertext()))
            self.assertIn('Следующий абзац.', ''.join(chapter.itertext()))

    def test_html_spine_missing_image_fails_closed(self):
        # Detection must not accidentally route a bad image into plain HTML extraction.
        from book import read_epub, BookError
        fixture(self.source, missing_image=True)
        self.rename_chapter_to_html()
        self.assertTrue(ep.has_raster_xhtml(self.source))
        with self.assertRaisesRegex(BookError, 'Undeclared or unsupported raster'):
            read_epub(self.source)

    def install_vector_fixture(self, svg: str) -> None:
        """Add a declared SVG in place of a decorative raster image."""
        with zipfile.ZipFile(self.source) as original:
            files = [(info, original.read(info.filename)) for info in original.infolist()]
        with zipfile.ZipFile(self.source, 'w') as archive:
            for info, data in files:
                if info.filename == 'OPS/package.opf':
                    data = data.replace(
                        b'</manifest>',
                        b'<item id="vector" href="vector.svg" media-type="image/svg+xml"/></manifest>')
                elif info.filename == 'OPS/chapter.xhtml':
                    data = data.replace(b'<img src="flower.jpg"', b'<img src="vector.svg"')
                archive.writestr(info, data)
            archive.writestr('OPS/vector.svg', svg.encode('utf-8'))

    def test_static_svg_in_html_spine_is_sha_bound_and_preserved_unchanged(self):
        from book import read_epub
        svg = ('<?xml version="1.0"?>'
               '<svg xmlns="http://www.w3.org/2000/svg" width="78" height="78">'
               '<defs><linearGradient id="color"><stop offset="0%" stop-color="#fff"/>'
               '</linearGradient></defs>'
               '<g fill="url(#color)"><path d="M0 0h10v10H0z"/></g></svg>')
        self.install_vector_fixture(svg)
        self.rename_chapter_to_html()
        chapters, identity = read_epub(self.source)
        report = ep.audit(self.source, chapters)
        vector = report['documents'][3]['images'][0]
        self.assertEqual(vector['path'], 'OPS/vector.svg')
        self.assertEqual(vector['media_type'], 'image/svg+xml')
        text = chapters[1].content.replace('Next paragraph.', 'Следующий абзац.')
        output = ep.build(
            self.source, [{'markdown': chapters[0].content}, {'markdown': text}],
            expected_source_sha256=report['source_sha256'], language='ru')
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(self.source) as before, zipfile.ZipFile(io.BytesIO(output)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            self.assertEqual(before.read('OPS/vector.svg'), after.read('OPS/vector.svg'))
            self.assertEqual(before.read('OPS/flower.jpg'), after.read('OPS/flower.jpg'))
            self.assertEqual(before.read('OPS/book.css'), after.read('OPS/book.css'))
            xhtml = ET.fromstring(after.read('OPS/chapter.html'))
            self.assertEqual(xhtml.find('.//{*}img').get('src'), 'vector.svg')
            self.assertIn('Следующий абзац.', ''.join(xhtml.itertext()))

    def test_certified_static_svg_passes_shared_source_coverage_gate(self):
        # The preserving extractor verifies inert SVGs, but the separate
        # durable coverage preflight must also recognize their MIME identity.
        from workflow.source_coverage import coverage_integrity_errors
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
               '<path d="M0 0h4v4H0z"/></svg>')
        self.install_vector_fixture(svg)
        self.rename_chapter_to_html()
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        book_dir = Path(self.temp.name) / 'sealed-book'
        extracted = book_dir / 'extracted'
        extracted.mkdir(parents=True)
        progress = {'chapters': []}
        for number, chapter in enumerate(chapters, 1):
            rel = f'extracted/chapter-{number:06d}.md'
            (book_dir / rel).write_text(chapter.content, encoding='utf-8')
            progress['chapters'].append({'number': number, 'source_path': rel})
        metadata = {
            'source_format': 'epub',
            'layout_mode': ep.MODE,
            'source': {'sha256': report['source_sha256'],
                       'size_bytes': self.source.stat().st_size},
        }
        coverage = book_dir / 'source-coverage.json'
        coverage.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(coverage_integrity_errors(book_dir, metadata, progress), [])

        vector = report['documents'][3]['images'][0]
        vector['media_type'] = 'text/html'
        coverage.write_text(json.dumps(report), encoding='utf-8')
        self.assertIn('Invalid preserved image identity for OPS/chapter.html',
                      coverage_integrity_errors(book_dir, metadata, progress))
        vector['media_type'] = 'image/svg+xml'
        vector['sha256'] = 'not-a-hash'
        coverage.write_text(json.dumps(report), encoding='utf-8')
        self.assertIn('Invalid preserved image identity for OPS/chapter.html',
                      coverage_integrity_errors(book_dir, metadata, progress))

    def test_svg_with_script_visible_text_external_reference_or_bad_gradient_is_rejected(self):
        cases = [
            '<script>alert(1)</script>',
            '<text>Do not hide these words</text>',
            '<image href="https://example.org/photo.png"/>',
            '<rect fill="url(https://example.org/a.svg)" width="1" height="1"/>',
            '<rect fill="url(#missing)" width="1" height="1"/>',
            '<foreignObject><p xmlns="http://www.w3.org/1999/xhtml">Words</p></foreignObject>',
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                fixture(self.source)
                svg = '<svg xmlns="http://www.w3.org/2000/svg">' + payload + '</svg>'
                self.install_vector_fixture(svg)
                with self.assertRaisesRegex(ep.PreservedEpubError, 'Unsafe SVG'):
                    ep.analyze(self.source)

    def test_url_word_break_and_multiple_links_preserve_original_link_subtrees(self):
        self.replace_xhtml(
            'OPS/intro.xhtml',
            b'<p class="intro"><span class="italic">Hello</span> world.</p>',
            b'<p>Visit <a href="https://first.example">first.<wbr class="break"/>example</a>'
            b' and <a href="https://second.example">second.example</a> today.</p>')
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        self.assertIn('first.example', chapters[0].content)
        self.assertIn('second.example', chapters[0].content)
        translated = chapters[0].content.replace('Visit ', 'Посетите ').replace(' today.', ' сегодня.')
        result = ep.build(
            self.source, [{'markdown': translated}, {'markdown': chapters[1].content}],
            expected_source_sha256=report['source_sha256'], language='ru')
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            p = ET.fromstring(archive.read('OPS/intro.xhtml')).find('.//{*}p')
            links = p.findall('{*}a')
            self.assertEqual(len(links), 2)
            self.assertEqual([a.get('href') for a in links],
                             ['https://first.example', 'https://second.example'])
            self.assertEqual(links[0].find('{*}wbr').get('class'), 'break')
            self.assertIn('Посетите', ''.join(p.itertext()))
            self.assertIn('сегодня.', ''.join(p.itertext()))

    def test_reordered_or_ambiguous_link_labels_fail_closed(self):
        self.replace_xhtml(
            'OPS/intro.xhtml',
            b'<p class="intro"><span class="italic">Hello</span> world.</p>',
            b'<p><a href="https://one.example">one.example</a> then '
            b'<a href="https://two.example">two.example</a>.</p>')
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        changed = chapters[0].content.replace(
            'one.example then two.example', 'two.example затем one.example')
        with self.assertRaisesRegex(ep.PreservedEpubError, 'Hyperlink order'):
            ep.build(self.source, [{'markdown': changed}, {'markdown': chapters[1].content}],
                     expected_source_sha256=report['source_sha256'], language='ru')

    def test_word_break_outside_link_is_rejected(self):
        self.replace_xhtml('OPS/intro.xhtml', b'</body>',
                           b'<p>Orphan<wbr/>break</p></body>')
        with self.assertRaisesRegex(ep.PreservedEpubError, 'Unmapped word-break'):
            ep.analyze(self.source)

    def install_linked_icon_credit(self, *, unlinked_text=False) -> None:
        intro = b'<p class="intro"><span class="italic">Hello</span> world.</p>'
        if unlinked_text:
            icon = (b'<p>Unlinked original prose '
                    b'<a href="https://example.org"><img src="flower.jpg"/>'
                    b'Publisher</a></p>')
        else:
            icon = (b'<p class="publisher-credit"><a href="https://example.org">'
                    b'<span class="image-frame"><img src="flower.jpg" alt="Publisher icon"/></span>'
                    b' <span>Created with Vellum</span></a></p>'
                    b'<p>Original readable prose.</p>')
        self.replace_xhtml('OPS/intro.xhtml', intro, icon)

    def test_linked_icon_brand_credit_is_preserved_and_never_sent_as_prose(self):
        self.install_linked_icon_credit()
        chapters = self.extracted()
        self.assertEqual(chapters[0].content,
                         '<!-- bt-block:000001 -->\nOriginal readable prose.\n')
        self.assertNotIn('Created with Vellum', chapters[0].content)
        coverage = ep.audit(self.source, chapters)
        self.assertEqual(coverage['chapter_count'], 2)
        translated = chapters[0].content.replace(
            'Original readable prose.', 'Переведённый литературный текст.')
        result = ep.build(
            self.source, [{'markdown': translated}, {'markdown': chapters[1].content}],
            expected_source_sha256=coverage['source_sha256'], language='ru')
        self.assertEqual(ep.validate(result, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            root = ET.fromstring(z.read('OPS/intro.xhtml'))
            credit, paragraph = root.findall('.//{*}p')
            self.assertEqual(credit.get('class'), 'publisher-credit')
            self.assertEqual(credit.find('.//{*}a').get('href'), 'https://example.org')
            self.assertEqual(credit.find('.//{*}img').get('src'), 'flower.jpg')
            self.assertIn('Created with Vellum', ''.join(credit.itertext()))
            self.assertEqual(''.join(paragraph.itertext()), 'Переведённый литературный текст.')

    def test_linked_icon_credit_cannot_be_mutated_in_epub_build(self):
        self.install_linked_icon_credit()
        chapters = self.extracted()
        coverage = ep.audit(self.source, chapters)
        result = ep.build(
            self.source, [{'markdown': c.content} for c in chapters],
            expected_source_sha256=coverage['source_sha256'], language='ru')
        tampered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(result)) as z:
            members = [(i, z.read(i.filename)) for i in z.infolist()]
        with zipfile.ZipFile(tampered, 'w') as z:
            for info, data in members:
                if info.filename == 'OPS/intro.xhtml':
                    self.assertIn(b'Created with Vellum', data)
                    data = data.replace(b'Created with Vellum', b'Changed publisher credit')
                z.writestr(info, data)
        with self.assertRaisesRegex(ep.PreservedEpubError, 'Protected linked-icon label changed'):
            ep.validate(tampered.getvalue(), source=self.source, expected_docs=5)

    def test_inline_image_with_independent_prose_remains_blocked(self):
        self.install_linked_icon_credit(unlinked_text=True)
        with self.assertRaisesRegex(ep.PreservedEpubError, 'Inline artwork inside a prose block'):
            ep.analyze(self.source)

    def install_vellum_toc(self):
        original = ('<h1>Contents</h1><p><a href="intro.xhtml">Intro</a></p>'
                    '<p><a href="chapter.xhtml">Chapter 1</a></p>')
        markup = ('<div role="doc-toc" xmlns:epub="http://www.idpf.org/2007/ops" '
                  'epub:type="toc"><div><h1>Contents</h1></div>'
                  '<div><p><a href="intro.xhtml#intro-start"><span>Intro</span></a></p>'
                  '<p><a href="chapter.xhtml">Chapter 1</a></p></div></div>'
                  '<div style="text-align:center"><p>'
                  '<a href="https://example.org/credit"><i>Publisher credit</i></a>'
                  '</p></div>')
        self.replace_xhtml('OPS/toc.xhtml', original.encode(), markup.encode())
        self.replace_xhtml('OPS/intro.xhtml', b'<p class="intro">',
                           b'<p id="intro-start" class="intro">')

    def test_vellum_toc_and_attribution_preserved_without_untranslated_literary_prose(self):
        self.install_vellum_toc()
        docs, identity = ep.analyze(self.source)
        self.assertEqual([d.kind for d in docs], ['artwork', 'toc', 'text', 'text', 'artwork'])
        chapters = self.extracted()
        self.assertEqual(len(chapters), 2)
        self.assertIn('Hello', chapters[0].content)
        coverage = ep.audit(self.source, chapters)
        self.assertEqual(coverage['documents'][1]['state'], 'preserved_toc')
        translated = chapters[0].content.replace('*Hello* world.', '*Привет* мир.')
        output = ep.build(self.source, [{'markdown': translated},
                                        {'markdown': chapters[1].content}],
                          expected_source_sha256=identity['source_sha256'], language='ru')
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(self.source) as before, zipfile.ZipFile(io.BytesIO(output)) as after:
            self.assertEqual(before.read('OPS/toc.xhtml'), after.read('OPS/toc.xhtml'))
            self.assertIn(b'Publisher credit', after.read('OPS/toc.xhtml'))
            self.assertIn('Привет', ''.join(ET.fromstring(after.read('OPS/intro.xhtml')).itertext()))

    def test_vellum_toc_invalid_links_and_uncovered_footer_prose_fail_closed(self):
        for old, new, failure in [
            (b'intro.xhtml#intro-start', b'intro.xhtml#missing', 'invalid TOC fragment'),
            (b'chapter.xhtml', b'missing.xhtml', 'invalid or duplicate TOC target'),
            (b'chapter.xhtml', b'intro.xhtml#intro-start', 'invalid or duplicate TOC target'),
            (b'chapter.xhtml', b'https://example.org/chapter', 'invalid TOC link'),
            (b'<i>Publisher credit</i></a>',
             b'<i>Publisher credit</i></a>Unexpected prose', 'unverified TOC footer'),
        ]:
            with self.subTest(failure=failure):
                fixture(self.source)
                self.install_vellum_toc()
                self.replace_xhtml('OPS/toc.xhtml', old, new)
                with self.assertRaisesRegex(ep.PreservedEpubError, failure):
                    ep.analyze(self.source)

    def test_vellum_toc_mixed_literary_prose_is_not_swallowed(self):
        self.install_vellum_toc()
        self.replace_xhtml('OPS/toc.xhtml', b'</body>',
                           b'<p>Chapter prose outside the verified table of contents.</p></body>')
        with self.assertRaisesRegex(ep.PreservedEpubError, 'invalid Vellum TOC wrapper'):
            ep.analyze(self.source)

    def test_nested_section_wrappers_preserve_layout_and_all_prose(self):
        # Two semantic section containers are NOT prose blocks themselves:
        # their heading and both paragraphs must be translated independently.
        self.replace_xhtml(
            'OPS/chapter.xhtml',
            b'<div><img ',
            b'<section id="chapter-main" class="story" epub:type="chapter" '
            b'xmlns:epub="http://www.idpf.org/2007/ops">'
            b'<h2>Chapter Heading</h2><div><img ')
        self.replace_xhtml(
            'OPS/chapter.xhtml',
            b'<p class="next">Next paragraph.</p>',
            b'<section id="inner"><p class="next">Next paragraph.</p>'
            b'</section></section>')
        original_sha = ep.analyze(self.source)[1]['source_sha256']
        chapters = self.extracted()
        self.assertEqual(len(chapters), 2)
        text = chapters[1].content
        self.assertEqual(text.count('<!-- bt-block:'), 3)
        self.assertIn('Chapter Heading', text)
        self.assertIn('Once upon a time.', text)
        self.assertIn('Next paragraph.', text)
        report = ep.audit(self.source, chapters)
        self.assertEqual(report['documents'][3]['text_blocks'], 3)
        translated = text.replace('Chapter Heading', 'Заголовок главы')
        translated = translated.replace('Once upon a time.', 'Однажды.')
        translated = translated.replace('Next paragraph.', 'Следующий абзац.')
        output = ep.build(
            self.source,
            [{'markdown': chapters[0].content}, {'markdown': translated}],
            expected_source_sha256=report['source_sha256'], language='ru')
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(io.BytesIO(output)) as out, zipfile.ZipFile(self.source) as original:
            root = ET.fromstring(out.read('OPS/chapter.xhtml'))
            outer = root.find('.//{*}section')
            self.assertIsNotNone(outer)
            self.assertEqual(outer.get('id'), 'chapter-main')
            self.assertEqual(outer.get('class'), 'story')
            self.assertEqual(
                outer.get('{http://www.idpf.org/2007/ops}type'), 'chapter')
            self.assertEqual(outer.find('{*}h2').text, 'Заголовок главы')
            self.assertEqual(outer.find('.//{*}section').get('id'), 'inner')
            self.assertEqual(outer.find('.//{*}section/{*}p').text, 'Следующий абзац.')
            self.assertEqual(outer.find('.//{*}p')[0].get('class'), 'initial')
            self.assertIn('Однажды.', ''.join(outer.itertext()))
            self.assertIsNotNone(outer.find('.//{*}img'))
            self.assertEqual(out.read('OPS/flower.jpg'), original.read('OPS/flower.jpg'))
            self.assertEqual(out.read('OPS/book.css'), original.read('OPS/book.css'))
        self.assertEqual(ep.analyze(self.source)[1]['source_sha256'], original_sha)

    def test_section_cannot_hide_unmapped_text_or_attributes(self):
        for section in (
            '<section>Lost prose<p>Visible block.</p></section>',
            '<section><p>Visible block.</p>Lost tail</section>',
            '<section hidden="hidden"><p>Hidden.</p></section>',
            '<section><script>alert(1)</script><p>Visible.</p></section>',
        ):
            with self.subTest(section=section):
                fixture(self.source)
                self.replace_xhtml(
                    'OPS/chapter.xhtml', b'</body>',
                    section.encode('utf-8') + b'</body>')
                with self.assertRaises(ep.PreservedEpubError):
                    ep.analyze(self.source)

    def test_image_only_section_is_preserved_without_translation(self):
        self.replace_xhtml(
            'OPS/end.xhtml',
            b'<img src="flower.jpg" alt=""/>',
            b'<section class="end-art"><img src="flower.jpg" alt=""/></section>')
        docs, _ = ep.analyze(self.source)
        self.assertEqual(docs[-1].kind, 'artwork')
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        output = ep.build(
            self.source, [{'markdown': chapter.content} for chapter in chapters],
            expected_source_sha256=report['source_sha256'], language='ru')
        with zipfile.ZipFile(self.source) as original, zipfile.ZipFile(io.BytesIO(output)) as result:
            self.assertEqual(original.read('OPS/end.xhtml'), result.read('OPS/end.xhtml'))

    def make_epub3_navigation(self, *, in_spine=None, off_spine=None) -> None:
        """Add a canonical in-spine TOC and an off-spine OPF nav resource."""
        wrapper = '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
        if in_spine is None:
            in_spine = (wrapper + '<h1>Contents</h1><ol>'
                        '<li><a href="chapter.xhtml">Chapter 1</a></li>'
                        '</ol></nav>')
        if off_spine is None:
            off_spine = (wrapper + '<h1>Contents</h1><ol>'
                         '<li><a href="intro.xhtml">Intro</a></li>'
                         '<li><a href="chapter.xhtml">Chapter 1</a></li>'
                         '</ol></nav>')
        with zipfile.ZipFile(self.source) as original:
            files = [(info, original.read(info.filename)) for info in original.infolist()]
        with zipfile.ZipFile(self.source, 'w') as z:
            for info, raw in files:
                if info.filename == 'OPS/toc.xhtml':
                    begin, _, tail = raw.partition(b'<body>')
                    self.assertIn(b'</body>', tail)
                    raw = (begin + b'<body>' + in_spine.encode('utf-8')
                           + b'</body></html>')
                elif info.filename == 'OPS/package.opf':
                    self.assertIn(b'</manifest>', raw)
                    raw = raw.replace(
                        b'</manifest>', b'<item id="nav-doc" href="nav.xhtml" '
                        b'media-type="application/xhtml+xml" properties="nav"/>'
                        b'</manifest>')
                z.writestr(info, raw)
            z.writestr(
                'OPS/nav.xhtml',
                ('<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                 '<title>Contents</title></head><body>' + off_spine +
                 '</body></html>').encode('utf-8'),
            )

    def test_epub3_nav_in_spine_and_off_spine_preserved_no_prose_lost(self):
        self.make_epub3_navigation()
        docs, identity = ep.analyze(self.source)
        self.assertEqual([doc.kind for doc in docs], ['artwork', 'toc', 'text', 'text', 'artwork'])
        chapters = self.extracted()
        self.assertEqual(len(chapters), 2)
        self.assertIn('Once upon a time.', chapters[1].content)
        report = ep.audit(self.source, chapters)
        self.assertEqual(report['chapter_count'], 2)
        self.assertEqual(report['documents'][1]['state'], 'preserved_toc')
        translated = [
            {'markdown': chapters[0].content.replace('*Hello* world.', '*Привет* мир.')},
            {'markdown': chapters[1].content.replace('Next paragraph.', 'Следующий абзац.')},
        ]
        output = ep.build(
            self.source, translated,
            expected_source_sha256=identity['source_sha256'], language='ru')
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(self.source) as original, zipfile.ZipFile(io.BytesIO(output)) as result:
            self.assertEqual(original.namelist(), result.namelist())
            for path in ('OPS/toc.xhtml', 'OPS/nav.xhtml', 'OPS/front.jpg',
                         'OPS/flower.jpg', 'OPS/book.css'):
                self.assertEqual(original.read(path), result.read(path))
            intro = ET.fromstring(result.read('OPS/intro.xhtml'))
            chapter = ET.fromstring(result.read('OPS/chapter.xhtml'))
            self.assertIn('Привет', ''.join(intro.itertext()))
            self.assertIn('Следующий абзац.', ''.join(chapter.itertext()))

    def test_epub3_navigation_rejects_broken_or_unsafe_toc(self):
        wrapper = '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
        valid_link = '<ol><li><a href="chapter.xhtml">Chapter 1</a></li></ol>'
        cases = [
            (wrapper + valid_link + '</nav><p>Unmapped body prose.</p>', 'outside the TOC'),
            (wrapper + '<p>Unmapped navigation prose.</p>' + valid_link + '</nav>', 'unsupported TOC element'),
            ('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="landmarks">'
             + valid_link + '</nav>', 'only explicit EPUB 3'),
            (wrapper + '<ol><li><a href="https://example.org">External</a></li></ol></nav>',
             'TOC reference outside'),
            (wrapper + '<ol><li><a href="chapter.xhtml#missing-id">Lost</a></li></ol></nav>',
             'broken TOC fragment'),
            (wrapper + '<ol><li><a href="missing.xhtml">Lost</a></li></ol></nav>',
             'TOC reference outside'),
            (wrapper + '<ol><li><a onclick="run()" href="chapter.xhtml">Unsafe</a></li></ol></nav>',
             'executable/hidden'),
            (wrapper + '<ol><li><a href="chapter.xhtml">Valid</a>extra prose</li></ol></nav>',
             'unmapped TOC tail'),
        ]
        for snippet, message in cases:
            with self.subTest(message=message, snippet=snippet):
                fixture(self.source)
                self.make_epub3_navigation(in_spine=snippet)
                with self.assertRaisesRegex(ep.PreservedEpubError, message):
                    ep.analyze(self.source)

    def test_off_spine_navigation_must_be_verified_not_just_marked_nav(self):
        for raw, message in [
            ('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
             '<ol><li><a href="intro.xhtml">Intro</a></li></ol></nav>'
             '<p>Not navigation.</p>', 'outside the TOC'),
            ('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
             '<ol><li><a href="intro.xhtml">Intro</a></li></ol></nav>'
             '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="landmarks"/>',
             'multiple EPUB navigation'),
        ]:
            with self.subTest(message=message):
                fixture(self.source)
                self.make_epub3_navigation(off_spine=raw)
                with self.assertRaisesRegex(ep.PreservedEpubError, message):
                    ep.analyze(self.source)

    def test_leaf_div_prose_is_independently_translated_without_rewriting_wrapper(self):
        self.replace_xhtml(
            'OPS/chapter.xhtml', b'</body>',
            b'<div id="dedication" class="epigraph">Remember the sea.</div>'
            b'<div id="poetry">Quiet <em>stars</em>.</div></body>')
        chapters = self.extracted()
        text = chapters[1].content
        self.assertEqual(text.count('<!-- bt-block:'), 4)
        self.assertIn('Remember the sea.', text)
        self.assertIn('Quiet *stars*.', text)
        report = ep.audit(self.source, chapters)
        self.assertEqual(report['documents'][3]['text_blocks'], 4)
        translated = text.replace('Remember the sea.', 'Помни море.')
        translated = translated.replace('Quiet *stars*.', 'Тихие *звёзды*.')
        output = ep.build(
            self.source,
            [{'markdown': chapters[0].content}, {'markdown': translated}],
            expected_source_sha256=report['source_sha256'], language='ru')
        with zipfile.ZipFile(io.BytesIO(output)) as result:
            chapter = ET.fromstring(result.read('OPS/chapter.xhtml'))
            dedication = next(x for x in chapter.iter() if x.get('id') == 'dedication')
            poetry = next(x for x in chapter.iter() if x.get('id') == 'poetry')
            self.assertEqual(dedication.tag.rsplit('}', 1)[-1], 'div')
            self.assertEqual(dedication.get('class'), 'epigraph')
            self.assertEqual(dedication.text, 'Помни море.')
            self.assertEqual(poetry.text, 'Тихие ')
            self.assertEqual(poetry.find('{*}em').text, 'звёзды')
            self.assertIsNotNone(chapter.find('.//{*}img'))
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)

    def test_mixed_div_with_nested_blocks_art_or_unmapped_inline_is_blocked(self):
        unsafe = [
            '<div>Unmapped text<p>Mapped paragraph.</p></div>',
            '<div>Unmapped text<img src="flower.jpg" alt=""/></div>',
            '<div>Unmapped <span id="anchor">fragment</span></div>',
            '<div>Unmapped first<br/>second line.</div>',
        ]
        for sample in unsafe:
            with self.subTest(sample=sample):
                fixture(self.source)
                self.replace_xhtml(
                    'OPS/chapter.xhtml', b'</body>',
                    sample.encode('utf-8') + b'</body>')
                with self.assertRaisesRegex(ep.PreservedEpubError, 'Unmapped'):
                    ep.analyze(self.source)

    def test_description_list_survives_layout_preserving_roundtrip(self):
        with zipfile.ZipFile(self.source) as archive:
            assets = {name: archive.read(name) for name in archive.namelist()}
        assets['OPS/intro.xhtml'] = assets['OPS/intro.xhtml'].replace(
            b'</body>', b'<dl><dt>Author</dt><dd>Example writer</dd></dl></body>')
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, value in assets.items():
                archive.writestr(name, value, compress_type=zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED)
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        output = ep.build(self.source, [{'markdown': chapter.content} for chapter in chapters],
                          expected_source_sha256=report['source_sha256'], language='ru')
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            document = ET.fromstring(archive.read('OPS/intro.xhtml'))
            self.assertEqual(document.find('.//{*}dt').text, 'Author')
            self.assertEqual(document.find('.//{*}dd').text, 'Example writer')

    def test_scene_break_survives_layout_preserving_roundtrip(self):
        with zipfile.ZipFile(self.source) as archive:
            assets = {name: archive.read(name) for name in archive.namelist()}
        assets['OPS/intro.xhtml'] = assets['OPS/intro.xhtml'].replace(
            b'</body>', b'<hr/><p>Another scene.</p></body>')
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, value in assets.items():
                archive.writestr(name, value, compress_type=zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED)
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        output = ep.build(self.source, [{'markdown': chapter.content} for chapter in chapters],
                          expected_source_sha256=report['source_sha256'], language='ru')
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            document = ET.fromstring(archive.read('OPS/intro.xhtml'))
            self.assertIsNotNone(document.find('.//{*}hr'))

    def test_inventory_order_images_and_toc(self):
        docs,_ = ep.analyze(self.source)
        self.assertEqual([d.kind for d in docs], ['artwork','toc','text','text','artwork'])
        self.assertEqual([d.title for d in docs if d.kind=='text'], ['Intro','Chapter 1'])
        self.assertEqual(len([img for d in docs for img in d.images]), 3)

    def test_literal_asterisks_inside_italic_are_not_confused_with_emphasis_markers(self):
        # Literal *grins* inside XHTML italics must survive escaping, source
        # coverage, and translation projection without extra Markdown stars.
        self.replace_xhtml(
            'OPS/chapter.xhtml',
            b'<p class="next">Next paragraph.</p>',
            b'<p class="next"><i>Maybe later. *grins*</i> '
            b'and literal *smiles* outside italics.</p>')
        chapters = self.extracted()
        markdown = chapters[1].content
        self.assertIn(r'*Maybe later. \*grins\**', markdown)
        self.assertIn(r'literal \*smiles\* outside', markdown)
        report = ep.audit(self.source, chapters)
        self.assertEqual(report['documents'][3]['text_blocks'], 2)
        output = ep.build(
            self.source, [{'markdown': x.content} for x in chapters],
            expected_source_sha256=report['source_sha256'], language='ru')
        self.assertEqual(ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            chapter = ET.fromstring(archive.read('OPS/chapter.xhtml'))
            paragraph = chapter.find('.//{*}p[@class="next"]')
            self.assertEqual(''.join(paragraph.itertext()),
                             'Maybe later. *grins* and literal *smiles* outside italics.')
            self.assertEqual(paragraph.find('{*}em').text, 'Maybe later. *grins*')

    def test_escaped_literals_and_bold_delimiters_roundtrip(self):
        from workflow.epub_preserve import _plain_markdown
        self.assertEqual(_plain_markdown(r'*aside \*literal\**'),
                         'aside *literal*')
        self.assertEqual(_plain_markdown(r'**bold \*literal\***'),
                         'bold *literal*')
        self.assertEqual(_plain_markdown(r'plain \*literal\*'),
                         'plain *literal*')

    def test_inline_emphasis_whitespace_keeps_exact_source_word_boundaries(self):
        # Covers XHTML like <i>felt </i>different; old recursive strip()
        # silently merged the adjacent words and failed independent coverage.
        self.replace_xhtml(
            'OPS/chapter.xhtml',
            b'<p class="next">Next paragraph.</p>',
            b'<p class="next">It <i>felt </i>different and stayed'
            b'<i> quite</i> clear with <span class="italic">warm </span>light'
            b' near <span>the </span>shore.</p>')
        chapters = self.extracted()
        text = chapters[1].content
        self.assertIn('It *felt* different', text)
        self.assertIn('stayed *quite* clear', text)
        self.assertIn('with *warm* light', text)
        self.assertIn('near the shore.', text)
        coverage = ep.audit(self.source, chapters)
        self.assertEqual(coverage['documents'][3]['text_blocks'], 2)
        translated = text.replace('It *felt* different', 'Это *казалось* другим')
        translated = translated.replace('near the shore.', 'возле берега.')
        output = ep.build(
            self.source,
            [{'markdown': chapters[0].content}, {'markdown': translated}],
            expected_source_sha256=coverage['source_sha256'], language='ru')
        self.assertEqual(
            ep.validate(output, source=self.source, expected_docs=5)['unit_count'], 2)
        with zipfile.ZipFile(io.BytesIO(output)) as result:
            chapter = ET.fromstring(result.read('OPS/chapter.xhtml'))
            paragraph = chapter.find('.//{*}p[@class="next"]')
            self.assertIn('Это', ''.join(paragraph.itertext()))
            self.assertIn('казалось', ''.join(paragraph.itertext()))
            self.assertIsNotNone(paragraph.find('.//{*}em'))

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

    def test_styled_quoted_and_italic_initials_survive_roundtrip(self):
        with zipfile.ZipFile(self.source) as z:
            assets = {name: z.read(name) for name in z.namelist()}
        body = assets['OPS/chapter.xhtml'].decode('utf-8')
        body = body.replace('<span class="initial">O</span>nce',
                            '<span class="initial">“O</span>nce')
        assets['OPS/chapter.xhtml'] = body.encode('utf-8')
        assets['OPS/book.css'] += b'\n.initial {font-style:italic;}'
        with zipfile.ZipFile(self.source, 'w') as z:
            for path, payload in assets.items():
                z.writestr(path, payload,
                           compress_type=zipfile.ZIP_STORED if path=='mimetype' else zipfile.ZIP_DEFLATED)
        chapters = self.extracted()
        report = ep.audit(self.source, chapters)
        output = ep.build(self.source, [{'markdown': c.content} for c in chapters],
                          expected_source_sha256=report['source_sha256'],language='ru')
        with zipfile.ZipFile(io.BytesIO(output)) as z:
            p = ET.fromstring(z.read('OPS/chapter.xhtml')).find('.//{*}p')
            self.assertEqual(p[0].text, '“O')
            self.assertEqual(p[0].get('class'), 'initial')

    def test_russian_opening_quote_does_not_break_styled_drop_cap(self):
        chapters = self.extracted()
        coverage = ep.audit(self.source, chapters)
        translated = chapters[1].content.replace(
            "Once upon a time.", "«Однажды всё случилось.»"
        )
        output = ep.build(
            self.source,
            [{"markdown": chapters[0].content}, {"markdown": translated}],
            expected_source_sha256=coverage["source_sha256"], language="ru",
        )
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            paragraph = ET.fromstring(archive.read("OPS/chapter.xhtml")).find(
                ".//{*}p[@class='first']"
            )
            self.assertEqual(paragraph.text, "«")
            self.assertEqual(paragraph[0].get("class"), "initial")
            self.assertEqual(paragraph[0].text, "О")
            self.assertIn("«Однажды всё случилось.»", "".join(paragraph.itertext()))

    def test_russian_dialogue_dash_keeps_letter_as_drop_cap(self):
        self.replace_xhtml(
            "OPS/chapter.xhtml",
            b'<span class="initial">O</span>nce',
            "<span class=\"initial\">“O</span>nce".encode("utf-8"),
        )
        chapters = self.extracted()
        coverage = ep.audit(self.source, chapters)
        translated = chapters[1].content.replace(
            "“Once upon a time.", "— Давай начнём."
        )
        output = ep.build(
            self.source,
            [{"markdown": chapters[0].content}, {"markdown": translated}],
            expected_source_sha256=coverage["source_sha256"], language="ru",
        )
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            paragraph = ET.fromstring(archive.read("OPS/chapter.xhtml")).find(
                ".//{*}p[@class='first']"
            )
            self.assertEqual(paragraph.text, "— ")
            self.assertEqual(paragraph[0].text, "Д")
            self.assertIn("— Давай начнём.", "".join(paragraph.itertext()))

    def test_whole_paragraph_translated_link_retains_original_href(self):
        self.replace_xhtml(
            "OPS/intro.xhtml",
            b'<p class="intro"><span class="italic">Hello</span> world.</p>',
            b'<p><a href="https://example.org/order">Order now!</a></p>',
        )
        chapters = self.extracted()
        coverage = ep.audit(self.source, chapters)
        translated = chapters[0].content.replace("Order now!", "Закажите сейчас!")
        output = ep.build(
            self.source,
            [{"markdown": translated}, {"markdown": chapters[1].content}],
            expected_source_sha256=coverage["source_sha256"], language="ru",
        )
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            link = ET.fromstring(archive.read("OPS/intro.xhtml")).find(".//{*}a")
            self.assertEqual(link.get("href"), "https://example.org/order")
            self.assertEqual("".join(link.itertext()), "Закажите сейчас!")

    def test_partial_link_needs_explicit_translated_label(self):
        self.replace_xhtml(
            "OPS/intro.xhtml",
            b'<p class="intro"><span class="italic">Hello</span> world.</p>',
            b'<p>Read <a href="https://example.org/more">more</a> today.</p>',
        )
        chapters = self.extracted()
        coverage = ep.audit(self.source, chapters)
        translated = chapters[0].content.replace(
            "Read more today.", "Читайте подробнее сегодня."
        )
        options = {
            "source": self.source,
            "chapters": [{"markdown": translated}, {"markdown": chapters[1].content}],
            "expected_source_sha256": coverage["source_sha256"],
            "language": "ru",
        }
        with self.assertRaisesRegex(ep.PreservedEpubError, "explicit label binding"):
            ep.build(**options)
        output = ep.build(
            **options,
            link_bindings={"OPS/intro.xhtml#1:1": "подробнее"},
        )
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            paragraph = ET.fromstring(archive.read("OPS/intro.xhtml")).find(".//{*}p")
            link = paragraph.find("{*}a")
            self.assertEqual(link.get("href"), "https://example.org/more")
            self.assertEqual("".join(link.itertext()), "подробнее")
            self.assertEqual("".join(paragraph.itertext()), "Читайте подробнее сегодня.")
        with self.assertRaisesRegex(ep.PreservedEpubError, "explicit label binding"):
            ep.build(
                **options,
                link_bindings={"OPS/intro.xhtml#2:1": "подробнее"},
            )
        with self.assertRaisesRegex(ep.PreservedEpubError, "lost or ambiguous"):
            ep.build(
                **options,
                link_bindings={"OPS/intro.xhtml#1:1": "nonexistent"},
            )

    def test_translated_italic_link_has_no_literal_markdown_markers(self):
        self.replace_xhtml(
            "OPS/intro.xhtml",
            b'<p class="intro"><span class="italic">Hello</span> world.</p>',
            b'<p><i><a href="https://example.org/promo">Shop here</a></i></p>',
        )
        chapters = self.extracted()
        coverage = ep.audit(self.source, chapters)
        translated = chapters[0].content.replace("Shop here", "Покупайте здесь")
        output = ep.build(
            self.source,
            [{"markdown": translated}, {"markdown": chapters[1].content}],
            expected_source_sha256=coverage["source_sha256"], language="ru",
        )
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            paragraph = ET.fromstring(archive.read("OPS/intro.xhtml")).find(".//{*}p")
            link = paragraph.find(".//{*}a")
            self.assertEqual(link.get("href"), "https://example.org/promo")
            self.assertEqual("".join(link.itertext()), "Покупайте здесь")
            self.assertEqual("".join(paragraph.itertext()), "Покупайте здесь")
            self.assertIsNotNone(paragraph.find("{*}em"))

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
