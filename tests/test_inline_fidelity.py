"""Fail-closed rich source extraction and inline EPUB rendering."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import book
from workflow.epub_output import render_markdown_xhtml


class RichSourceTests(unittest.TestCase):
    def test_html_preserves_bold_italic_code(self):
        chapters = book.split_html_document(
            "<html><body><h1>Title</h1><p>Hello <em>quiet</em> and "
            "<strong>loud</strong> <code>fn()</code>.</p></body></html>", "Fallback",
        )
        self.assertEqual(len(chapters), 1)
        self.assertIn("Hello *quiet* and **loud** `fn()`.", chapters[0].content)

    def test_html_rejects_unsupported_image_instead_of_dropping(self):
        with self.assertRaisesRegex(book.BookError, "img"):
            book.split_html_document(
                "<html><body><p>See this <img src='diagram.png'/></p></body></html>",
                "Images",
            )

    def test_nested_formatting_is_preserved_in_epub(self):
        xhtml = render_markdown_xhtml(
            "Test", "# Heading\n\nNormal *italic* **bold *within*** and `code`.\n",
            language="en",
        ).decode("utf-8")
        self.assertIn("<em>italic</em>", xhtml)
        self.assertIn("<strong>bold <em>within</em></strong>", xhtml)
        self.assertIn("<code>code</code>", xhtml)


if __name__ == "__main__":
    unittest.main()
