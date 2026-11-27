"""Authored standalone HTML fixtures: preserve prose and note links."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from book import BookError, extract_chapters
from workflow.epub_output import build_epub_bytes, EpubOutputError, render_markdown_xhtml

FIXTURE = """<!DOCTYPE html><html><head><title>Sample</title></head><body>
<div id="preface"><h2 class="toc-heading">Preface</h2><h1>Sample</h1>
<p>See <a href="#endnotes">notes</a>.</p></div>
<div id="chapters"><div class="meta group"><h2 class="heading">Chapter 1</h2></div>
<div class="userstuff"><p>First <em>passage</em>.</p></div>
<div class="meta group"><h2 class="heading">Chapter 2</h2></div>
<div class="userstuff"><p>Second passage.</p><p><a href="#endnotes2">Note</a></p></div>
<div id="endnotes2"><p>Chapter note.</p></div></div>
<div id="afterword"><h2 class="toc-heading">Afterword</h2>
<div id="endnotes"><p>Final note.</p></div></div></body></html>"""


class HtmlExtractionLinksTests(unittest.TestCase):
    def extract(self, text=FIXTURE):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.html"
            path.write_text(text, encoding="utf-8")
            return extract_chapters(path, "html")[0]

    def test_ao3_units_and_notes(self):
        chapters = self.extract()
        self.assertEqual([c.title for c in chapters],
                         ["Preface", "Chapter 1", "Chapter 2", "Afterword"])
        self.assertIn("[notes](#endnotes)", chapters[0].content)
        self.assertIn("First *passage*.", chapters[1].content)
        self.assertIn("<!-- bt-anchor:endnotes2 -->", chapters[2].content)
        self.assertIn("Chapter note.", chapters[2].content)
        self.assertIn("<!-- bt-anchor:endnotes -->", chapters[3].content)
        self.assertIn("Final note.", chapters[3].content)

    def test_missing_anchor_fails_closed(self):
        with self.assertRaises(BookError):
            self.extract(FIXTURE.replace('id="endnotes"', 'id="other"'))

    def test_duplicate_anchor_fails_closed(self):
        with self.assertRaises(BookError):
            self.extract(FIXTURE.replace('id="afterword"', 'id="endnotes"'))

    def build(self, units):
        return build_epub_bytes(book_slug="sample", title="Sample", author=None,
                                language="en", fingerprint="a" * 64, units=units)

    def test_epub_resolves_cross_unit_note(self):
        units = [{"number": i, "title": c.title, "slug": f"unit-{i}",
                  "markdown": c.content} for i, c in enumerate(self.extract(), 1)]
        with zipfile.ZipFile(io.BytesIO(self.build(units))) as archive:
            first = archive.read("EPUB/text/001-unit-1.xhtml").decode()
            third = archive.read("EPUB/text/003-unit-3.xhtml").decode()
            fourth = archive.read("EPUB/text/004-unit-4.xhtml").decode()
        self.assertIn('href="004-unit-4.xhtml#endnotes"', first)
        self.assertIn('href="#endnotes2"', third)
        self.assertIn('id="endnotes"', fourth)

    def test_unresolved_global_anchor_rejected(self):
        with self.assertRaises(EpubOutputError):
            self.build([{"number": 1, "title": "Sample", "slug": "unit",
                         "markdown": "[note](#missing)"}])

    def test_direct_render_still_rejects_dangling_anchor(self):
        with self.assertRaises(EpubOutputError):
            render_markdown_xhtml("Sample", "[note](#missing)", language="en")


if __name__ == "__main__":
    unittest.main()
