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


class CorpusScaleTests(unittest.TestCase):
    def test_epub_with_205_spine_units_restores_as_one_complete_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "scripts").mkdir(parents=True)
            shutil.copy2(BOOK_SCRIPT, repo / "scripts" / "book.py")
            shutil.copy2(CORPUS_SCRIPT, repo / "scripts" / "corpus.py")
            if TEMPLATES.exists():
                shutil.copytree(TEMPLATES, repo / "docs" / "templates")

            source = repo / "large.epub"
            count = 205
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
                manifest = "\n".join(
                    f'<item id="c{i}" href="chapter{i}.xhtml" media-type="application/xhtml+xml"/>'
                    for i in range(1, count + 1)
                )
                spine = "\n".join(f'<itemref idref="c{i}"/>' for i in range(1, count + 1))
                zf.writestr(
                    "OEBPS/content.opf",
                    f"""<?xml version="1.0" encoding="UTF-8"?>
                    <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                        <dc:title>Large Book</dc:title>
                        <dc:creator>Example Author</dc:creator>
                        <dc:language>en</dc:language>
                      </metadata>
                      <manifest>{manifest}</manifest>
                      <spine>{spine}</spine>
                    </package>""",
                )
                for i in range(1, count + 1):
                    zf.writestr(
                        f"OEBPS/chapter{i}.xhtml",
                        f"<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Chapter {i}</h1><p>Body {i}.</p></body></html>",
                    )

            expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            extract = subprocess.run(
                [sys.executable, str(repo / "scripts" / "book.py"), "extract", str(source), "--slug", "large", "--target-language", "ru"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(extract.returncode, 0, msg=extract.stderr)

            book = repo / "books" / "large"
            progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(len(progress["chapters"]), count)
            self.assertEqual(len(list((book / "extracted").glob("*.md"))), count)

            shutil.rmtree(book / "extracted")
            (book / "extracted").mkdir()
            (book / "source" / "large.epub").unlink()

            restore = subprocess.run(
                [
                    sys.executable,
                    str(repo / "scripts" / "corpus.py"),
                    "restore",
                    "large",
                    str(source),
                    "--expected-sha256",
                    expected_sha,
                ],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(restore.returncode, 0, msg=restore.stderr)
            self.assertEqual(len(list((book / "extracted").glob("*.md"))), count)

            validate = subprocess.run(
                [sys.executable, str(repo / "scripts" / "book.py"), "validate", "large"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(validate.returncode, 0, msg=validate.stderr)


if __name__ == "__main__":
    unittest.main()
