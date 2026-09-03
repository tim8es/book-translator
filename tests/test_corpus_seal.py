import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOK_SCRIPT = PROJECT_ROOT / "scripts" / "book.py"
CORPUS_SCRIPT = PROJECT_ROOT / "scripts" / "corpus.py"
TEMPLATES = PROJECT_ROOT / "docs" / "templates"


class CorpusSealTests(unittest.TestCase):
    def test_seal_records_source_and_every_extracted_hash(self):
        self.assertTrue(CORPUS_SCRIPT.is_file(), "scripts/corpus.py must provide corpus sealing")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "scripts").mkdir(parents=True)
            shutil.copy2(BOOK_SCRIPT, repo / "scripts" / "book.py")
            shutil.copy2(CORPUS_SCRIPT, repo / "scripts" / "corpus.py")
            if TEMPLATES.exists():
                shutil.copytree(TEMPLATES, repo / "docs" / "templates")
            source = repo / "sample.md"
            source.write_text("# A\n\nOne.\n\n# B\n\nTwo.\n", encoding="utf-8")
            expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()

            extract = subprocess.run(
                [sys.executable, str(repo / "scripts" / "book.py"), "extract", str(source), "--slug", "sample", "--target-language", "ru"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(extract.returncode, 0, msg=extract.stderr)
            seal = subprocess.run(
                [sys.executable, str(repo / "scripts" / "corpus.py"), "seal", "sample"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(seal.returncode, 0, msg=seal.stderr)

            manifest = json.loads((repo / "books" / "sample" / "source-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_sha256"], expected_sha)
            self.assertEqual(manifest["chapter_count"], 2)
            self.assertEqual(len(manifest["extracted"]), 2)
            for item in manifest["extracted"]:
                self.assertEqual(len(item["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
