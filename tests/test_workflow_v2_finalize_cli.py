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
REVISION = "0123456789abcdef"

if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import workflow_v2.finalize as finalize_module


class WorkflowV2FinalizeRenderingTests(unittest.TestCase):
    def snapshot(self):
        return {
            "schema": "completion-report-v1",
            "book_slug": "demo",
            "workflow_revision": REVISION,
            "state_revisions": {
                "metadata": "metadata-revision",
                "progress": "progress-revision",
                "review_ledger": "ledger-revision",
            },
            "lifecycle": {
                "pending": 0,
                "extracted": 0,
                "translated": 0,
                "reviewed": 2,
            },
            "corpus": {
                "state": "verified",
                "storage_mode": "private_external",
                "source_attached": False,
            },
            "review": {
                "summary": {
                    "total_units": 2,
                    "pass": 2,
                    "corrections_required": 0,
                    "missing": 0,
                    "stale": 0,
                    "untranslated": 0,
                    "pass_coverage": {"passed": 2, "total": 2, "percent": 100.0},
                    "duplicate_records": 0,
                }
            },
            "quality_gates": {
                "structural_valid": True,
                "corpus_verified": True,
                "zero_active_claims": True,
                "translations_complete": True,
                "review_pass_coverage_complete": True,
                "all_reviewed": True,
            },
        }

    def test_completion_markdown_renderers_are_deterministic_and_timestamp_free(self):
        render_state = getattr(finalize_module, "render_state_markdown", None)
        render_gates = getattr(finalize_module, "render_quality_gates_markdown", None)
        self.assertIsNotNone(render_state, "render_state_markdown is not implemented")
        self.assertIsNotNone(render_gates, "render_quality_gates_markdown is not implemented")

        snapshot = self.snapshot()
        first_state = render_state(snapshot)
        second_state = render_state(snapshot)
        first_gates = render_gates(snapshot)
        second_gates = render_gates(snapshot)

        self.assertEqual(first_state.encode("utf-8"), second_state.encode("utf-8"))
        self.assertEqual(first_gates.encode("utf-8"), second_gates.encode("utf-8"))
        combined = first_state + first_gates
        self.assertNotIn("generated_at", combined)
        self.assertIn("demo", first_state)
        self.assertIn(REVISION, first_state)
        self.assertIn("progress-revision", first_state)
        self.assertIn("private_external", first_state)
        self.assertIn("2/2", first_state)
        self.assertIn("[x]", first_gates)
        self.assertIn("verified corpus", first_gates.lower())
        self.assertIn("100%", first_gates)


class WorkflowV2FinalizeCliTests(unittest.TestCase):
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

    def initialize_ready_book(self, slug="sample", *, private_source=False, keep_claim=False):
        source = self.repo / f"{slug}.md"
        source.write_text("# One\n\nAlpha.\n", encoding="utf-8")
        extract_args = [
            "extract",
            str(source),
            "--slug",
            slug,
            "--target-language",
            "ru",
        ]
        if private_source:
            extract_args.append("--private-source")
        self.run_cli(*extract_args)

        book = self.repo / "books" / slug
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

        self.run_cli(
            "claim",
            slug,
            "1",
            "--role",
            "reviewer",
            "--session-id",
            "reviewer-a",
            "--base-commit",
            "dispatch-commit",
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
            "review-commit-a",
            "--json",
        )
        if not keep_claim:
            self.run_cli(
                "release",
                slug,
                "1",
                "--session-id",
                "reviewer-a",
                "--json",
            )
        return book

    @staticmethod
    def report_bytes(book):
        return {
            name: (book / name).read_bytes()
            for name in ("STATE.md", "FINAL_QUALITY_GATES.md", "REVIEW_REPORT.md")
        }

    def test_finalize_promotes_writes_reports_and_reruns_idempotently(self):
        book = self.initialize_ready_book()
        first = self.run_cli("finalize", "sample", "--session-id", "finalizer-a")
        self.assertNotIn("Traceback", first.stderr)

        progress_path = book / "progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual([chapter["status"] for chapter in progress["chapters"]], ["reviewed"])
        first_progress = progress_path.read_bytes()
        first_reports = self.report_bytes(book)
        self.assertFalse((book / ".workflow" / "finalization.json").exists())

        second = self.run_cli("finalize", "sample", "--session-id", "finalizer-b")
        self.assertNotIn("Traceback", second.stderr)
        self.assertEqual(progress_path.read_bytes(), first_progress)
        self.assertEqual(self.report_bytes(book), first_reports)
        self.assertFalse((book / ".workflow" / "finalization.json").exists())

        json_result = self.run_cli(
            "finalize", "sample", "--session-id", "finalizer-c", "--json"
        )
        payload = json.loads(json_result.stdout)
        self.assertEqual(
            json_result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        self.assertEqual(payload["schema"], "completion-report-v1")
        self.assertEqual(payload["book_slug"], "sample")
        self.assertEqual(payload["quality_gates"]["all_reviewed"], True)
        self.assertEqual(self.report_bytes(book), first_reports)

    def test_finalize_failure_is_clean_and_does_not_partially_promote(self):
        for scenario in ("malformed-ledger", "invalid-corpus"):
            with self.subTest(scenario=scenario):
                slug = scenario.replace("-", "_")
                book = self.initialize_ready_book(slug)
                progress_path = book / "progress.json"
                before = progress_path.read_bytes()
                if scenario == "malformed-ledger":
                    (book / "review-ledger.json").write_text("{not-json\n", encoding="utf-8")
                else:
                    manifest_path = book / "source-manifest.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["extracted"][0]["sha256"] = "0" * 64
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

                result = self.run_cli(
                    "finalize", slug, "--session-id", "finalizer-a", expect=1
                )
                self.assertIn("ERROR:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(progress_path.read_bytes(), before)
                for name in ("STATE.md", "FINAL_QUALITY_GATES.md", "REVIEW_REPORT.md"):
                    self.assertFalse((book / name).exists())
                self.assertFalse((book / ".workflow" / "finalization.json").exists())

    def test_finalize_active_claim_blocks_and_preserves_claim(self):
        book = self.initialize_ready_book(keep_claim=True)
        progress_path = book / "progress.json"
        before = progress_path.read_bytes()
        claim_path = book / ".workflow" / "claims" / "chapter-000001.json"
        self.assertTrue(claim_path.is_file())
        claim_before = claim_path.read_bytes()

        result = self.run_cli(
            "finalize", "sample", "--session-id", "finalizer-a", expect=1
        )
        self.assertIn("active claims", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(progress_path.read_bytes(), before)
        self.assertEqual(claim_path.read_bytes(), claim_before)
        self.assertFalse((book / ".workflow" / "finalization.json").exists())

    def test_finalize_private_external_succeeds_without_source_binary(self):
        book = self.initialize_ready_book("private", private_source=True)
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))
        source_path = book / "source" / metadata["source_file"]
        self.assertEqual(metadata["source"]["storage_mode"], "private_external")
        self.assertFalse(source_path.exists())

        self.run_cli("finalize", "private", "--session-id", "finalizer-a")
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(progress["chapters"][0]["status"], "reviewed")
        self.assertFalse(source_path.exists())
        state = (book / "STATE.md").read_text(encoding="utf-8")
        self.assertIn("private_external", state)
        self.assertIn("verified", state)


if __name__ == "__main__":
    unittest.main()
