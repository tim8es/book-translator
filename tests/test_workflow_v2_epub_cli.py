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
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from workflow_v2.epub_output import validate_epub_bytes, validate_output_manifest


REVISION = "0123456789abcdef"


class WorkflowV2EpubCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        (self.repo / ".book-translator-install.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": "https://github.com/tim8es/book-translator",
                    "requested_ref": "refactor/workflow-engine-v2",
                    "resolved_revision": REVISION,
                    "install_root": ".",
                }
            )
            + "\n",
            encoding="utf-8",
        )

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

    def canonical_json(self, result):
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return payload

    def initialize_book(self, slug="sample", *, private=False):
        source = self.repo / f"{slug}.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        args = [
            "extract",
            str(source),
            "--slug",
            slug,
            "--target-language",
            "ru",
        ]
        if private:
            args.append("--private-source")
        self.run_cli(*args)
        return self.repo / "books" / slug

    def mark_translated(self, book):
        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        translation = book / progress["chapters"][0]["translation_path"]
        translation.parent.mkdir(parents=True, exist_ok=True)
        translation.write_text("# Один\n\nАльфа.\n", encoding="utf-8")
        progress["chapters"][0]["status"] = "translated"
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def initialize_final_book(self, slug="sample", *, private=False):
        book = self.initialize_book(slug, private=private)
        self.mark_translated(book)
        self.run_cli(
            "claim",
            slug,
            "1",
            "--role",
            "reviewer",
            "--session-id",
            "reviewer-a",
            "--base-commit",
            "review-dispatch",
            "--json",
        )
        self.run_cli(
            "review-record",
            slug,
            "1",
            "--outcome",
            "PASS",
            "--session-id",
            "reviewer-a",
            "--review-commit",
            "review-commit",
            "--json",
        )
        self.run_cli(
            "release",
            slug,
            "1",
            "--session-id",
            "reviewer-a",
            "--json",
        )
        self.run_cli("finalize", slug, "--session-id", "finalizer-a", "--json")
        return book

    def test_final_epub_build_writes_valid_artifact_manifest_and_current_status(self):
        book = self.initialize_final_book()
        result = self.run_cli("build", "sample", "--format", "epub")
        self.assertNotIn("Traceback", result.stderr)

        artifact = book / "output" / "sample.epub"
        manifest_path = book / "output" / "manifest.json"
        self.assertTrue(artifact.is_file())
        self.assertTrue(manifest_path.is_file())
        validate_epub_bytes(artifact.read_bytes(), expected_unit_count=1)
        manifest = validate_output_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        self.assertFalse(manifest["preview"])
        self.assertEqual(manifest["artifact_path"], "output/sample.epub")

        status_result = self.run_cli("build-status", "sample", "--format", "epub", "--json")
        status = self.canonical_json(status_result)
        self.assertEqual(status["state"], "current")
        self.assertEqual(status["artifact_path"], "output/sample.epub")

    def test_default_markdown_build_remains_backward_compatible(self):
        book = self.initialize_final_book()
        self.run_cli("build", "sample")
        self.assertTrue((book / "output" / "sample.md").is_file())
        self.assertFalse((book / "output" / "sample.epub").exists())

    def test_final_epub_rejects_unreviewed_but_explicit_preview_succeeds(self):
        book = self.initialize_book()
        self.mark_translated(book)
        final = self.run_cli("build", "sample", "--format", "epub", expect=1)
        self.assertIn("ERROR:", final.stderr)
        self.assertFalse((book / "output" / "sample.epub").exists())

        self.run_cli("build", "sample", "--format", "epub", "--allow-unreviewed")
        manifest = validate_output_manifest(
            json.loads((book / "output" / "manifest.json").read_text(encoding="utf-8"))
        )
        self.assertTrue(manifest["preview"])
        status = self.canonical_json(
            self.run_cli("build-status", "sample", "--format", "epub", "--allow-unreviewed", "--json")
        )
        self.assertEqual(status["state"], "current")
        self.assertTrue(status["preview"])

    def test_private_external_final_build_succeeds_without_source_binary(self):
        book = self.initialize_final_book("private", private=True)
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        source_path = book / "source" / metadata["source_file"]
        self.assertFalse(source_path.exists())

        self.run_cli("build", "private", "--format", "epub")
        self.assertTrue((book / "output" / "private.epub").is_file())
        self.assertFalse(source_path.exists())
        status = self.canonical_json(
            self.run_cli("build-status", "private", "--format", "epub", "--json")
        )
        self.assertEqual(status["state"], "current")

    def test_output_extension_mismatch_fails_without_writes(self):
        book = self.initialize_final_book()
        result = self.run_cli(
            "build",
            "sample",
            "--format",
            "epub",
            "--output",
            "wrong.md",
            expect=1,
        )
        self.assertIn("ERROR:", result.stderr)
        self.assertFalse((book / "output" / "wrong.md").exists())
        self.assertFalse((book / "output" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
