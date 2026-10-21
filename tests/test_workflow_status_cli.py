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
WORKFLOW = PROJECT_ROOT / "scripts" / "workflow"
REVISION = "0123456789abcdef"


class WorkflowStatusCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BOOK_SCRIPT, self.repo / "scripts" / "book.py")
        shutil.copy2(CORPUS_SCRIPT, self.repo / "scripts" / "corpus.py")
        shutil.copytree(WORKFLOW, self.repo / "scripts" / "workflow")
        shutil.copy2(PROJECT_ROOT / "agent-manifest.json", self.repo / "agent-manifest.json")
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

    def run_corpus(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "corpus.py"), *args],
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
    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def canonical_json(self, result):
        payload = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        )
        return payload

    def test_plan_sessions_cli_is_read_only_with_remote_head(self):
        book = self.initialize_book()
        progress_path = book / "progress.json"
        before = progress_path.read_bytes()
        result = self.run_book(
            "plan-sessions", "sample", "--sessions", "3",
            "--base-commit", REVISION, "--json",
        )
        plan = self.canonical_json(result)
        self.assertEqual(plan["remaining_units"], 1)
        self.assertEqual(len(plan["sessions"]), 1)
        self.assertEqual(plan["sessions"][0]["chapter_numbers"], [1])
        self.assertEqual(plan["sessions"][0]["context"]["base_commit"], REVISION)
        self.assertEqual(progress_path.read_bytes(), before)

    def test_saved_workpack_next_step_does_not_mutate_progress(self):
        book = self.initialize_book()
        progress = (book / "progress.json").read_bytes()
        payload = self.canonical_json(self.run_book(
            "plan-sessions", "sample", "--sessions", "2",
            "--base-commit", REVISION, "--save", "--json",
        ))
        plan_path = (book / ".workflow" / "session-dispatches"
                     / (payload["dispatch_id"] + ".json"))
        self.assertTrue(plan_path.is_file())
        saved = self.canonical_json(self.run_book(
            "session-next", "sample", "--dispatch-id", payload["dispatch_id"],
            "--session-id", payload["sessions"][0]["session_id"], "--json",
        ))
        self.assertEqual(saved["action"], "claim")
        self.assertEqual(saved["operation"], "translate")
        self.assertEqual((book / "progress.json").read_bytes(), progress)

    def test_benchmark_cli_reports_only_supplied_measurements(self):
        observations = {
            "schema_version": 1, "dataset_id": "test-only",
            "evaluation_protocol": "independent-review",
            "runs": [
                {
                    "run_id": label, "sessions": sessions,
                    "unit_ids": ["chapter-000001"],
                    "reviewed_unit_ids": ["chapter-000001"],
                    "source_words": 100, "elapsed_seconds": seconds,
                    "github_requests": 10, "manual_interventions": 2,
                    "major_findings": 0, "critical_findings": 0,
                }
                for label, sessions, seconds in (
                    ("baseline", 1, 1000), ("candidate", 2, 450)
                )
            ],
        }
        path = self.repo / "observations.json"
        path.write_text(json.dumps(observations), encoding="utf-8")
        report = self.canonical_json(self.run_book(
            "benchmark-sessions", str(path), "--json"
        ))
        self.assertEqual(report["dataset_id"], "test-only")
        self.assertTrue(report["comparisons"][0]["meets_provisional_2x_target"])
        self.assertEqual(json.loads(path.read_text()), observations)

    def test_wave_cli_plans_starts_pins_context_and_blocks_second_wave(self):
        book_dir = self.initialize_book()
        preview = self.canonical_json(
            self.run_book("wave-plan", "sample", "--count", "1")
        )
        self.assertEqual(preview["unit_ids"], ["chapter-000001"])
        started = self.canonical_json(
            self.run_book("wave-start", "sample", "--count", "1")
        )
        wave_id = started["wave_id"]
        self.assertEqual(len(wave_id), 32)
        self.assertEqual(
            self.canonical_json(self.run_book("wave-status", "sample"))["manifest"]["wave_id"],
            wave_id,
        )
        context = self.canonical_json(
            self.run_book("memory-context", "sample", "--unit-id", "chapter-000001")
        )
        self.assertEqual(context["entries"], [])
        dispatch = self.canonical_json(
            self.run_book(
                "wave-dispatch", "sample", "--sessions", "2",
                "--base-commit", REVISION
            )
        )
        action = self.canonical_json(
            self.run_book(
                "wave-next", "sample",
                "--dispatch-id", dispatch["dispatch_id"],
                "--session-id", dispatch["sessions"][0]["session_id"]
            )
        )
        self.assertEqual(action["action"], "claim")
        load = action["load_plan"]
        self.assertEqual(load["role"], "translator")
        self.assertEqual(load["phase"], "after_claim")
        self.assertEqual(load["instruction_paths"], [
            "AGENTS.md", "docs/TRANSLATION.md", "docs/WAVE_WORKER.md",
        ])
        self.assertEqual(
            [item["kind"] for item in load["inputs"]],
            ["original", "glossary", "style_guide"],
        )
        self.assertEqual(
            load["inputs"][0]["path"], "books/sample/extracted/001-one.md",
        )
        self.assertFalse(load["source_content_included"])
        self.assertNotIn("Alpha.", json.dumps(load, ensure_ascii=False))
        self.assertEqual(load["memory"]["revision"], started["memory_revision"])
        self.assertEqual(
            load["memory"]["snapshot_path"],
            (f"books/sample/.workflow/literary-memory/snapshots/"
             f"{started['memory_revision']}.json"),
        )
        # Restarting the same chat is a read-only, identical context plan.
        self.assertEqual(
            self.canonical_json(self.run_book(
                "wave-next", "sample",
                "--dispatch-id", dispatch["dispatch_id"],
                "--session-id", dispatch["sessions"][0]["session_id"],
            )),
            action,
        )
        pinned = action["context"]["shared_state_revisions"]
        self.canonical_json(self.run_book(
            "claim", "sample", "1", "--role", "translator",
            "--session-id", dispatch["sessions"][0]["session_id"],
            "--base-commit", REVISION,
            "--glossary-revision", pinned["glossary"],
            "--style-guide-revision", pinned["style_guide"], "--json",
        ))
        resumed = self.canonical_json(self.run_book(
            "wave-next", "sample",
            "--dispatch-id", dispatch["dispatch_id"],
            "--session-id", dispatch["sessions"][0]["session_id"],
        ))
        self.assertEqual(resumed["action"], "work")
        self.assertEqual(resumed["load_plan"]["phase"], "live_claim")
        self.assertEqual(resumed["load_plan"]["inputs"], load["inputs"])
        self.assertEqual(
            self.canonical_json(self.run_book("wave-status", "sample"))["state"]["phase"],
            "ACTIVE",
        )
        self.assertEqual(
            self.run_book("wave-start", "sample", "--count", "1", expect=1).returncode,
            1,
        )

    def test_wave_next_fails_closed_if_active_memory_pointer_drifts(self):
        book = self.initialize_book()
        self.canonical_json(self.run_book(
            "wave-start", "sample", "--count", "1",
        ))
        dispatch = self.canonical_json(self.run_book(
            "wave-dispatch", "sample", "--sessions", "1",
            "--base-commit", REVISION,
        ))
        pointer = book / ".workflow" / "literary-memory" / "active.json"
        pointer.write_text(json.dumps({"revision": "e" * 64}) + "\n", encoding="utf-8")
        response = self.run_book(
            "wave-next", "sample",
            "--dispatch-id", dispatch["dispatch_id"],
            "--session-id", dispatch["sessions"][0]["session_id"],
            expect=1,
        )
        self.assertIn("literary memory", response.stderr)
        self.assertNotIn('"load_plan"', response.stdout)

    def test_status_and_resume_json_are_deterministic_bounded_and_read_only(self):
        book = self.initialize_book()
        progress = json.loads((book / "progress.json").read_text(encoding="utf-8"))
        source_path = progress["chapters"][0]["source_path"]
        durable = [
            book / "metadata.json",
            book / "progress.json",
            book / "review-ledger.json",
            book / "source-manifest.json",
        ]
        before = {path.name: self.sha256(path) for path in durable}

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertEqual(status["lifecycle"], {"extracted": 1, "pending": 0, "reviewed": 0, "translated": 0})
        self.assertEqual(status["reviews"], {"corrections_required": 0, "missing": 0, "pass": 0, "stale": 0, "untranslated": 1})
        self.assertEqual(status["corpus"]["state"], "verified")
        self.assertEqual(status["corpus"]["storage_mode"], "embedded")
        self.assertTrue(status["corpus"]["source_attached"])
        self.assertEqual(status["claims"], [])
        self.assertTrue(status["valid"])

        resume = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resume["operation"], "translate")
        self.assertEqual(resume["unit_id"], "chapter-000001")
        self.assertEqual(resume["context"]["role"], "translator")
        self.assertEqual(resume["context"]["contracts"], ["AGENTS.md", "docs/TRANSLATION.md"])
        self.assertEqual(
            resume["context"]["files"],
            [
                "metadata.json",
                "progress.json",
                "glossary.md",
                "style-guide.md",
                source_path,
            ],
        )

        after = {path.name: self.sha256(path) for path in durable}
        self.assertEqual(after, before)
        self.assertFalse((book / "status.json").exists())

    def test_private_source_status_is_verified_without_binary(self):
        book = self.initialize_book(private=True)
        metadata = json.loads((book / "metadata.json").read_text(encoding="utf-8"))

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertTrue(status["valid"])
        self.assertEqual(status["corpus"]["state"], "verified")
        self.assertEqual(status["corpus"]["storage_mode"], "private_external")
        self.assertEqual(status["corpus"]["source_file"], "sample.md")
        self.assertEqual(status["corpus"]["source_sha256"], metadata["source"]["sha256"])
        self.assertEqual(status["corpus"]["source_size_bytes"], metadata["source"]["size_bytes"])
        self.assertFalse(status["corpus"]["source_attached"])

        resume = self.canonical_json(self.run_book("resume", "sample", "--json"))
        self.assertEqual(resume["operation"], "translate")

    def test_explicit_source_missing_manifest_blocks_resume(self):
        book = self.initialize_book()
        (book / "source-manifest.json").unlink()

        result = self.run_book("resume", "sample", "--json", expect=1)
        payload = self.canonical_json(result)
        self.assertEqual(payload["operation"], "blocked")
        self.assertEqual(payload["reason"], "preflight_failed")
        self.assertTrue(any("source-manifest.json" in error for error in payload["errors"]))

    def test_missing_explicit_source_is_invalid_current_state(self):
        book = self.initialize_book()
        metadata_path = book / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("source", None)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (book / "source-manifest.json").unlink()

        status = self.canonical_json(self.run_book("status", "sample", "--json"))
        self.assertFalse(status["valid"])
        self.assertEqual(status["corpus"]["state"], "invalid")
        self.assertTrue(any("source" in error.lower() for error in status["errors"]))

        resume = self.canonical_json(self.run_book("resume", "sample", "--json", expect=1))
        self.assertEqual(resume["operation"], "blocked")

    def test_resume_blocks_on_real_corpus_hash_mismatch(self):
        book = self.initialize_book()
        extracted = next((book / "extracted").glob("*.md"))
        extracted.write_text(extracted.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

        result = self.run_book("resume", "sample", "--json", expect=1)
        payload = self.canonical_json(result)

        self.assertEqual(payload["operation"], "blocked")
        self.assertEqual(payload["reason"], "preflight_failed")
        self.assertTrue(any("hash mismatch" in error for error in payload["errors"]))

    def test_human_status_and_resume_are_concise(self):
        self.initialize_book()

        status = self.run_book("status", "sample")
        self.assertIn("sample: valid", status.stdout)
        self.assertIn("lifecycle", status.stdout)
        self.assertIn("corpus=verified", status.stdout)
        self.assertIn("source=embedded", status.stdout)

        resume = self.run_book("resume", "sample")
        self.assertIn("next=translate", resume.stdout)
        self.assertIn("chapter-000001", resume.stdout)


if __name__ == "__main__":
    unittest.main()
