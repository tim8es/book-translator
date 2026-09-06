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
WORKFLOW_V2 = PROJECT_ROOT / "scripts" / "workflow_v2"
TEMPLATES = PROJECT_ROOT / "docs" / "templates"


class WorkflowV2PrivateSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
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

    def make_markdown(self, name="sample.md", text="# One\n\nAlpha.\n"):
        source = self.repo / name
        source.write_text(text, encoding="utf-8")
        return source

    def test_new_embedded_book_records_identity_and_is_sealed(self):
        source = self.make_markdown()
        source_bytes = source.read_bytes()
        expected_sha = hashlib.sha256(source_bytes).hexdigest()

        self.run_cli(
            "book.py",
            "extract",
            str(source),
            "--slug",
            "sample",
            "--target-language",
            "ru",
        )

        book = self.repo / "books" / "sample"
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(
            metadata["source"],
            {
                "storage_mode": "embedded",
                "filename": "sample.md",
                "size_bytes": len(source_bytes),
                "sha256": expected_sha,
            },
        )
        self.assertEqual(manifest["source_storage_mode"], "embedded")
        self.assertEqual(manifest["source_size_bytes"], len(source_bytes))
        self.assertEqual(manifest["source_sha256"], expected_sha)
        self.assertTrue((book / "source" / "sample.md").is_file())
        self.run_cli("corpus.py", "verify", "sample")

    def test_new_private_book_is_sealed_without_source_binary(self):
        source = self.make_markdown("private.md", "# One\n\nSecret source.\n")
        source_bytes = source.read_bytes()
        expected_sha = hashlib.sha256(source_bytes).hexdigest()

        self.run_cli(
            "book.py",
            "extract",
            str(source),
            "--slug",
            "private-book",
            "--target-language",
            "ru",
            "--private-source",
        )

        book = self.repo / "books" / "private-book"
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(metadata["source"]["storage_mode"], "private_external")
        self.assertEqual(metadata["source"]["filename"], "private.md")
        self.assertEqual(metadata["source"]["size_bytes"], len(source_bytes))
        self.assertEqual(metadata["source"]["sha256"], expected_sha)
        self.assertEqual(manifest["source_storage_mode"], "private_external")
        self.assertFalse((book / "source" / "private.md").exists())
        self.run_cli("book.py", "validate", "private-book")
        self.run_cli("corpus.py", "verify", "private-book")

    def test_explicit_source_schema_rejects_filename_disagreement(self):
        source = self.make_markdown()
        source_bytes = source.read_bytes()
        self.run_cli(
            "book.py",
            "extract",
            str(source),
            "--slug",
            "sample",
            "--target-language",
            "ru",
        )

        book = self.repo / "books" / "sample"
        metadata_path = book / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["source"] = {
            "storage_mode": "embedded",
            "filename": "different.md",
            "size_bytes": len(source_bytes),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli("book.py", "validate", "sample", expect=1)
        self.assertIn("source.filename", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
