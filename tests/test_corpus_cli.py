import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOK_SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
CORPUS_SCRIPT = PROJECT_ROOT / "scripts" / "corpus.py"
TEMPLATES = PROJECT_ROOT / "docs" / "templates"


class CorpusCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        self.assertTrue(CORPUS_SCRIPT.is_file(), "scripts/corpus.py must provide batch source-corpus recovery")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        if TEMPLATES.exists():
            shutil.copytree(TEMPLATES, self.repo / "docs" / "templates")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, script, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / script), *args],
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

    def make_epub(self, path: Path, body_suffix: str = "") -> None:
        with zipfile.ZipFile(path, "w") as zf:
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
                    <item id="c3" href="chapter3.xhtml" media-type="application/xhtml+xml"/>
                  </manifest>
                  <spine>
                    <itemref idref="c1"/>
                    <itemref idref="c2"/>
                    <itemref idref="c3"/>
                  </spine>
                </package>""",
            )
            for number, title in enumerate(("First", "Second", "Third"), start=1):
                zf.writestr(
                    f"OEBPS/chapter{number}.xhtml",
                    f"<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>{title}</h1><p>Body {number}{body_suffix}.</p></body></html>",
                )

    def test_restore_rebuilds_complete_corpus_without_mutating_translation_state(self):
        source = self.repo / "sample.epub"
        self.make_epub(source)
        expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()

        self.run_cli("book.py", "extract", str(source), "--slug", "sample", "--target-language", "ru")
        book = self.repo / "books" / "sample"
        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        translation_path = book / progress["chapters"][0]["translation_path"]
        translation_path.parent.mkdir(parents=True, exist_ok=True)
        translation_path.write_text("# Первая\n\nПеревод.\n", encoding="utf-8")
        progress["chapters"][0]["status"] = "reviewed"
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        shutil.rmtree(book / "extracted")
        (book / "extracted").mkdir()
        (book / "source" / "sample.epub").unlink()
        self.run_cli("book.py", "validate", "sample", expect=1)

        self.run_cli(
            "corpus.py",
            "restore",
            "sample",
            str(source),
            "--expected-sha256",
            expected_sha,
        )

        restored_progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual(restored_progress, progress)
        self.assertEqual(translation_path.read_text(encoding="utf-8"), "# Первая\n\nПеревод.\n")
        self.assertEqual(len(list((book / "extracted").glob("*.md"))), 3)
        self.assertTrue((book / "source" / "sample.epub").is_file())
        manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_sha256"], expected_sha)
        self.assertEqual(len(manifest["extracted"]), 3)
        self.run_cli("book.py", "validate", "sample")

    def test_restore_rejects_source_with_wrong_sha_before_writing(self):
        source = self.repo / "sample.epub"
        self.make_epub(source)
        expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        self.run_cli("book.py", "extract", str(source), "--slug", "sample", "--target-language", "ru")

        wrong = self.repo / "wrong.epub"
        self.make_epub(wrong, body_suffix=" changed")
        book = self.repo / "books" / "sample"
        shutil.rmtree(book / "extracted")
        (book / "extracted").mkdir()

        result = self.run_cli(
            "corpus.py",
            "restore",
            "sample",
            str(wrong),
            "--expected-sha256",
            expected_sha,
            expect=1,
        )
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertEqual(list((book / "extracted").glob("*.md")), [])


if __name__ == "__main__":
    unittest.main()
