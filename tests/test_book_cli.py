import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
TEMPLATES = PROJECT_ROOT / "docs" / "templates"


class BookCliSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        if TEMPLATES.exists():
            shutil.copytree(TEMPLATES, self.repo / "docs" / "templates")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "book.py"), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            expect,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def test_extract_markdown_creates_complete_book_state(self):
        source = self.repo / "sample.md"
        source.write_text(
            "# Chapter One\n\nFirst paragraph.\n\n# Chapter Two\n\nSecond paragraph.\n",
            encoding="utf-8",
        )

        self.run_cli(
            "extract",
            str(source),
            "--slug",
            "sample-book",
            "--source-language",
            "en",
            "--target-language",
            "ru",
        )

        book = self.repo / "books" / "sample-book"
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["source_format"], "markdown")
        self.assertEqual(metadata["chapter_count"], 2)
        self.assertEqual([c["status"] for c in progress["chapters"]], ["extracted", "extracted"])
        self.assertTrue((book / progress["chapters"][0]["source_path"]).is_file())
        self.assertTrue((book / "glossary.md").is_file())
        self.assertTrue((book / "style-guide.md").is_file())
        self.run_cli("validate", "sample-book")

    def test_extract_records_install_provenance_in_book_metadata(self):
        (self.repo / ".book-translator-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": "https://github.com/tim8es/book-translator",
                    "requested_ref": "agent-compatibility-and-skill",
                    "resolved_revision": "0123456789abcdef",
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        source = self.repo / "sample.md"
        source.write_text("# A\n\nOne.\n\n# B\n\nTwo.\n", encoding="utf-8")

        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")

        metadata = json.loads((self.repo / "books" / "sample" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(
            metadata["workflow"],
            {
                "repository": "https://github.com/tim8es/book-translator",
                "requested_ref": "agent-compatibility-and-skill",
                "resolved_revision": "0123456789abcdef",
                "review_evidence": "review-ledger-v1",
            },
        )

    def test_build_requires_reviewed_by_default(self):
        source = self.repo / "sample.txt"
        source.write_text(
            "Chapter 1\n\nOriginal one.\n\nChapter 2\n\nOriginal two.\n",
            encoding="utf-8",
        )
        self.run_cli(
            "extract",
            str(source),
            "--slug",
            "sample-book",
            "--source-language",
            "en",
            "--target-language",
            "ru",
        )

        book = self.repo / "books" / "sample-book"
        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        for chapter in progress["chapters"]:
            translated = book / chapter["translation_path"]
            translated.parent.mkdir(parents=True, exist_ok=True)
            translated.write_text(f"# Translation {chapter['number']}\n\nText.\n", encoding="utf-8")
            chapter["status"] = "translated"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        self.run_cli("validate", "sample-book")
        self.run_cli("build", "sample-book", expect=1)
        self.run_cli("build", "sample-book", "--allow-unreviewed")

        # This smoke test predates machine review evidence and only verifies the
        # build command's lifecycle-state filter, so keep its final state legacy.
        metadata_path = book / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["workflow"].pop("review_evidence", None)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (book / "review-ledger.json").unlink()

        for chapter in progress["chapters"]:
            chapter["status"] = "reviewed"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.run_cli("build", "sample-book")
        self.assertTrue((book / "output" / "sample-book.md").is_file())

    def test_validate_requires_style_guide(self):
        source = self.repo / "sample.md"
        source.write_text("# A\n\nOne.\n\n# B\n\nTwo.\n", encoding="utf-8")
        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")
        (self.repo / "books" / "sample" / "style-guide.md").unlink()
        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("Missing style-guide.md", result.stderr)

    def test_validate_rejects_unsupported_explicit_schema_version(self):
        source = self.repo / "sample.md"
        source.write_text("# A\n\nOne.\n\n# B\n\nTwo.\n", encoding="utf-8")
        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")

        metadata_path = self.repo / "books" / "sample" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["schema_version"] = 2
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = self.run_cli("validate", "sample", expect=1)
        self.assertIn("unsupported version 2", result.stderr)

    def test_validate_accepts_legacy_state_without_rewriting(self):
        source = self.repo / "sample.md"
        source.write_text("# A\n\nOne.\n\n# B\n\nTwo.\n", encoding="utf-8")
        self.run_cli("extract", str(source), "--slug", "sample", "--target-language", "ru")

        book = self.repo / "books" / "sample"
        paths = [book / "metadata.json", book / "progress.json"]
        original_bytes = {}
        for path in paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            del data["schema_version"]
            content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            path.write_bytes(content)
            original_bytes[path.name] = content

        self.run_cli("validate", "sample")

        for path in paths:
            self.assertEqual(path.read_bytes(), original_bytes[path.name])

    def test_extract_minimal_epub_uses_spine_order_and_metadata(self):
        source = self.repo / "sample.epub"
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles>
                    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
                  </rootfiles>
                </container>""",
            )
            zf.writestr(
                "OEBPS/content.opf",
                """<?xml version="1.0" encoding="UTF-8"?>
                <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                    <dc:title>Example Book</dc:title>
                    <dc:creator>Example Author</dc:creator>
                    <dc:language>en</dc:language>
                  </metadata>
                  <manifest>
                    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
                    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
                  </manifest>
                  <spine>
                    <itemref idref="c1"/>
                    <itemref idref="c2"/>
                  </spine>
                </package>""",
            )
            zf.writestr(
                "OEBPS/chapter1.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>First</h1><p>Alpha.</p></body></html>""",
            )
            zf.writestr(
                "OEBPS/chapter2.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Second</h1><p>Beta.</p></body></html>""",
            )

        self.run_cli("extract", str(source), "--slug", "epub-book", "--target-language", "ru")
        book = self.repo / "books" / "epub-book"
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["title"], "Example Book")
        self.assertEqual(metadata["author"], "Example Author")
        self.assertEqual(metadata["source_language"], "en")
        self.assertEqual([c["title"] for c in progress["chapters"]], ["First", "Second"])


if __name__ == "__main__":
    unittest.main()