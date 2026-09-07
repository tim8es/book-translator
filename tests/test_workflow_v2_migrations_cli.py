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
CANONICAL = "https://github.com/tim8es/book-translator"
OLD = "legacy-revision"
NEW = "0123456789abcdef"
OTHER = "fedcba9876543210"


class WorkflowV2MigrationsCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW_V2, self.repo / "scripts" / "workflow_v2")
        self.install_path = self.repo / ".book-translator-install.json"
        self.install_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "canonical_repository": CANONICAL,
                    "requested_ref": "refactor/workflow-engine-v2",
                    "resolved_revision": NEW,
                    "install_root": ".",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_book(self, *args, expect=0):
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

    def initialize_book(self, *, private=False):
        source = self.repo / "sample.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        args = ["extract", str(source), "--slug", "sample", "--target-language", "ru"]
        if private:
            args.append("--private-source")
        self.run_book(*args)
        return self.repo / "books" / "sample"

    @staticmethod
    def _write_json(path: Path, payload):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def make_legacy(self, book: Path, *, reviewed=False):
        metadata_path = book / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("schema_version", None)
        metadata["workflow"] = {
            "repository": CANONICAL,
            "requested_ref": "legacy-ref",
            "resolved_revision": OLD,
        }
        self._write_json(metadata_path, metadata)

        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        progress.pop("schema_version", None)
        if reviewed:
            chapter = progress["chapters"][0]
            translation_path = book / chapter["translation_path"]
            translation_path.parent.mkdir(parents=True, exist_ok=True)
            translation_path.write_text("Перевод.\n", encoding="utf-8")
            chapter["status"] = "reviewed"
        self._write_json(progress_path, progress)

        for name in ("review-ledger.json", "source-manifest.json"):
            path = book / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("schema_version", None)
            self._write_json(path, payload)

    @staticmethod
    def snapshot(book: Path):
        return {
            path.relative_to(book).as_posix(): path.read_bytes()
            for path in sorted(book.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def canonical_json(result):
        payload = json.loads(result.stdout)
        if result.returncode == 0:
            assert result.stdout == json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        return payload

    def test_legacy_v0_upgrade_records_revisions_downgrade_and_deterministic_json(self):
        book = self.initialize_book()
        self.make_legacy(book, reviewed=True)

        result = self.run_book("workflow-upgrade", "sample", "--to", NEW, "--json")
        payload = self.canonical_json(result)

        self.assertEqual(payload["book_slug"], "sample")
        self.assertEqual(payload["from_revision"], OLD)
        self.assertEqual(payload["to_revision"], NEW)
        self.assertEqual(payload["outcome"], "changed")
        self.assertEqual(payload["lifecycle_downgrades"], [1])
        self.assertEqual(
            payload["migrated_paths"],
            ["source-manifest.json", "review-ledger.json", "progress.json", "metadata.json"],
        )

        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        ledger = json.loads((book / "review-ledger.json").read_text(encoding="utf-8"))
        manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(progress["schema_version"], 1)
        self.assertEqual(ledger["schema_version"], 1)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(metadata["workflow"]["resolved_revision"], NEW)
        self.assertEqual(metadata["workflow"]["requested_ref"], "refactor/workflow-engine-v2")
        self.assertEqual(metadata["workflow"]["review_evidence"], "review-ledger-v1")
        self.assertEqual(metadata["workflow"]["upgrade_history"][-1]["from_revision"], OLD)
        self.assertEqual(metadata["workflow"]["upgrade_history"][-1]["to_revision"], NEW)
        self.assertEqual(progress["chapters"][0]["status"], "translated")
        self.assertFalse((book / ".workflow" / "migration.json").exists())

    def test_target_mismatch_is_concise_and_read_only(self):
        book = self.initialize_book()
        self.make_legacy(book)
        before = self.snapshot(book)

        result = self.run_book("workflow-upgrade", "sample", "--to", OTHER, expect=1)

        self.assertIn("installed", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertEqual(self.snapshot(book), before)

    def test_malformed_legacy_fixture_fails_concisely_without_mutation(self):
        book = self.initialize_book()
        self.make_legacy(book)
        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        progress["chapters"][0].pop("title")
        self._write_json(progress_path, progress)
        before = self.snapshot(book)

        result = self.run_book("workflow-upgrade", "sample", "--to", NEW, expect=1)

        self.assertIn("title", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertEqual(self.snapshot(book), before)

    def test_private_source_upgrade_never_restores_source_binary(self):
        book = self.initialize_book(private=True)
        self.make_legacy(book)
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        source_path = book / "source" / metadata["source_file"]
        self.assertFalse(source_path.exists())

        payload = self.canonical_json(
            self.run_book("workflow-upgrade", "sample", "--to", NEW, "--json")
        )

        self.assertEqual(payload["outcome"], "changed")
        self.assertFalse(source_path.exists())
        manifest = json.loads((book / "source-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_storage_mode"], "private_external")
        self.assertEqual(manifest["source_sha256"], metadata["source"]["sha256"])

    def test_ordinary_validate_does_not_silently_upgrade_legacy_state(self):
        book = self.initialize_book()
        self.make_legacy(book)
        before = self.snapshot(book)

        result = self.run_book("validate", "sample", expect=1)

        self.assertIn("source-manifest.json", result.stderr)
        self.assertEqual(self.snapshot(book), before)
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        self.assertNotIn("schema_version", metadata)
        self.assertNotIn("schema_version", progress)
        self.assertEqual(metadata["workflow"]["resolved_revision"], OLD)

    def test_second_upgrade_is_byte_idempotent_unchanged(self):
        book = self.initialize_book()
        self.make_legacy(book)
        first = self.canonical_json(
            self.run_book("workflow-upgrade", "sample", "--to", NEW, "--json")
        )
        self.assertEqual(first["outcome"], "changed")
        before = self.snapshot(book)

        second_result = self.run_book("workflow-upgrade", "sample", "--to", NEW, "--json")
        second = self.canonical_json(second_result)

        self.assertEqual(second["outcome"], "unchanged")
        self.assertEqual(second["migrated_paths"], [])
        self.assertEqual(second["lifecycle_downgrades"], [])
        self.assertEqual(self.snapshot(book), before)


if __name__ == "__main__":
    unittest.main()
