import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import book
import corpus


class WorkflowV2CorpusManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book_dir = Path(self.tmp.name) / "book"
        self.book_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, data):
        path = self.book_dir / "source-manifest.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def valid_manifest(self):
        return {
            "schema_version": 1,
            "source_file": "source.md",
            "source_format": "markdown",
            "source_sha256": "a" * 64,
            "chapter_count": 1,
            "extracted": [
                {
                    "number": 1,
                    "title": "One",
                    "path": "extracted/001-one.md",
                    "sha256": "b" * 64,
                }
            ],
        }

    def test_load_source_manifest_rejects_unsupported_explicit_version(self):
        data = self.valid_manifest()
        data["schema_version"] = 2
        self.write_manifest(data)

        with self.assertRaises(book.BookError) as ctx:
            corpus.load_source_manifest(self.book_dir)

        self.assertIn("unsupported version 2", str(ctx.exception))

    def test_load_source_manifest_rejects_invalid_structural_hash(self):
        data = self.valid_manifest()
        data["source_sha256"] = "not-a-sha"
        self.write_manifest(data)

        with self.assertRaises(book.BookError) as ctx:
            corpus.load_source_manifest(self.book_dir)

        self.assertIn("source_sha256", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
