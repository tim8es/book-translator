import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts import book


class BuildHeadingNormalizationTests(unittest.TestCase):
    def test_build_uses_author_facing_title_and_preserves_fractional_numbering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_root = book.REPO_ROOT
            book.REPO_ROOT = root
            try:
                book_dir = root / "books" / "sample"
                for directory in ("source", "extracted", "translated", "output"):
                    (book_dir / directory).mkdir(parents=True, exist_ok=True)

                (book_dir / "source" / "source.txt").write_text("source\n", encoding="utf-8")
                (book_dir / "glossary.md").write_text("# Glossary\n", encoding="utf-8")
                (book_dir / "style-guide.md").write_text("# Style Guide\n", encoding="utf-8")

                chapters = [
                    {
                        "number": 1,
                        "title": "Chapter 18: Chapter 17.5",
                        "slug": "chapter-18-chapter-175",
                        "source_path": "extracted/001.md",
                        "translation_path": "translated/001.md",
                        "status": "reviewed",
                    },
                    {
                        "number": 2,
                        "title": "Chapter 19: Chapter 19",
                        "slug": "chapter-19-chapter-19",
                        "source_path": "extracted/002.md",
                        "translation_path": "translated/002.md",
                        "status": "reviewed",
                    },
                    {
                        "number": 3,
                        "title": "Chapter 20",
                        "slug": "chapter-20",
                        "source_path": "extracted/003.md",
                        "translation_path": "translated/003.md",
                        "status": "reviewed",
                    },
                ]

                metadata = {
                    "schema_version": 1,
                    "title": "Sample",
                    "source_language": "en",
                    "target_language": "ru",
                    "source_format": "txt",
                    "source_file": "source.txt",
                    "chapter_count": len(chapters),
                    "workflow": {
                        "repository": book.CANONICAL_REPOSITORY,
                        "resolved_revision": "test",
                    },
                }
                progress = {
                    "schema_version": 1,
                    "book_slug": "sample",
                    "chapters": chapters,
                }
                (book_dir / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
                )
                (book_dir / "progress.json").write_text(
                    json.dumps(progress, ensure_ascii=False), encoding="utf-8"
                )

                for number in range(1, 4):
                    (book_dir / "extracted" / f"{number:03d}.md").write_text(
                        f"## Source {number}\n", encoding="utf-8"
                    )

                (book_dir / "translated" / "001.md").write_text(
                    "## Глава 18: Глава 17.5\n\nПервый текст.\n", encoding="utf-8"
                )
                (book_dir / "translated" / "002.md").write_text(
                    "## Глава 19: Глава 19\n\nВторой текст.\n", encoding="utf-8"
                )
                (book_dir / "translated" / "003.md").write_text(
                    "## Глава 20\n\nТретий текст.\n", encoding="utf-8"
                )

                result = book.build_command(
                    argparse.Namespace(slug="sample", allow_unreviewed=False, output="sample.md")
                )
                self.assertEqual(result, 0)

                output = (book_dir / "output" / "sample.md").read_text(encoding="utf-8")
                self.assertIn("## Глава 17.5\n\nПервый текст.", output)
                self.assertIn("## Глава 19\n\nВторой текст.", output)
                self.assertIn("## Глава 20\n\nТретий текст.", output)
                self.assertNotIn("## Глава 18: Глава 17.5", output)
                self.assertNotIn("## Глава 19: Глава 19", output)
            finally:
                book.REPO_ROOT = old_root


if __name__ == "__main__":
    unittest.main()
