import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from io import BytesIO

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow.epub_output import build_epub_bytes, render_markdown_xhtml


XHTML_NS = "http://www.w3.org/1999/xhtml"


class EpubLayoutRegressionTests(unittest.TestCase):
    def test_plain_russian_heading_and_notes_are_structured(self):
        content = (
            "Глава 7. Дом в ритме твоего сердца\\n\\n"
            "Примечания к главе\\n\\n"
            "Предупреждение автора.\\n\\n"
            "ГЛАВА 7\\n\\n"
            "Обычный абзац с **жирным** и *курсивом*.\\n"
        )
        xml = render_markdown_xhtml("Chapter 7: Home", content, language="ru")
        root = ET.fromstring(xml)
        body = root.find(f"{{{XHTML_NS}}}body")
        self.assertEqual(
            [node.tag.rsplit("}", 1)[-1] for node in body[:5]],
            ["h1", "h2", "p", "h3", "p"],
        )
        self.assertEqual(len(root.findall(f".//{{{XHTML_NS}}}strong")), 1)
        self.assertEqual(len(root.findall(f".//{{{XHTML_NS}}}em")), 1)

    def test_explicit_h2_chapter_is_promoted_and_no_second_h1_added(self):
        root = ET.fromstring(
            render_markdown_xhtml(
                "Original title",
                "## Глава 25. Больше никаких секретов\\n\\n"
                "### Примечания к главе\\n\\n"
                "Начало.\\n",
                language="ru",
            )
        )
        self.assertEqual(len(root.findall(f".//{{{XHTML_NS}}}h1")), 1)
        self.assertEqual(len(root.findall(f".//{{{XHTML_NS}}}h2")), 1)

    def test_toc_uses_russian_rendered_heading_not_original_title(self):
        data = build_epub_bytes(
            book_slug="layout-regression",
            title="The Claiming Moon",
            author="Example",
            language="ru",
            units=[{
                "number": 1,
                "slug": "chapter-1",
                "title": "Chapter 1: Original",
                "markdown": "Глава 1. Русское название\\n\\nТекст.",
            }],
            fingerprint="a" * 64,
        )
        with ZipFile(BytesIO(data)) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
            chapter = archive.read("EPUB/text/001-chapter-1.xhtml").decode("utf-8")
        self.assertIn("Глава 1. Русское название", nav)
        self.assertNotIn("Chapter 1: Original</a>", nav)
        self.assertIn("<h1>Глава 1. Русское название</h1>", chapter)


if __name__ == "__main__":
    unittest.main()
