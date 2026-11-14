"""Regression checks for private workspace CI book-file validation."""
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "verify_changed_books.py"
spec = importlib.util.spec_from_file_location("verify_changed_books", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ChangedBookChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.book = self.root / "books" / "sample"
        self.book.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, *paths):
        return module.verify_changed_books(list(paths), self.root, run_canonical=False)

    def test_valid_json_and_utf8_markdown(self):
        (self.book / "progress.json").write_text('{"schema_version": 1}', encoding="utf-8")
        (self.book / "draft.md").write_text("# Текст\n", encoding="utf-8")
        self.assertEqual([], self.check("books/sample/progress.json", "books/sample/draft.md"))

    def test_invalid_json_and_duplicate_keys_are_rejected(self):
        (self.book / "progress.json").write_text('{"a":1,"a":2}', encoding="utf-8")
        self.assertTrue(self.check("books/sample/progress.json"))
        (self.book / "progress.json").write_text('{"a":', encoding="utf-8")
        self.assertTrue(self.check("books/sample/progress.json"))

    def test_epub_integrity(self):
        epub = self.book / "preview.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
        self.assertEqual([], self.check("books/sample/preview.epub"))
        epub.write_bytes(b"broken epub")
        self.assertTrue(self.check("books/sample/preview.epub"))

    def test_parent_traversal_is_rejected(self):
        self.assertTrue(self.check("books/sample/../../secret.json"))

    def test_deleted_legacy_draft_does_not_block_others(self):
        self.assertEqual([], self.check("books/sample/deleted.md"))


if __name__ == "__main__":
    unittest.main()
