"""Regression tests for bounded, durable correction and conservative Actions admission."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker
from omniroute import ModelError


class BoundedCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.slug = "synthetic-batch-test"
        self.book = self.root / "books" / self.slug
        (self.book / "extracted").mkdir(parents=True)
        (self.book / "translated").mkdir()
        source = self.book / "extracted" / "026.md"
        source.write_text("Illustrative synthetic source; no book excerpts.\n", encoding="utf-8")
        target = self.book / "translated" / "026.md"
        target.write_text(" ".join(f"piece-{i:02d}" for i in range(1, 11)), encoding="utf-8")
        self.original_translation = target.read_bytes()
        (self.book / "glossary.md").write_text("Glossary fixture\n", encoding="utf-8")
        (self.book / "style-guide.md").write_text("Style fixture\n", encoding="utf-8")
        self.chapter = {
            "unit_id": "chapter-000026", "number": 26, "status": "translated",
            "source_path": "extracted/026.md", "translation_path": "translated/026.md",
            "translation_acceptance": {
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "translation_sha256": hashlib.sha256(self.original_translation).hexdigest(),
            },
        }
        self.findings = [
            {"id": f"meaning-{i}", "resolved": False,
             "translation_anchor": {"quote": f"piece-{i:02d}", "occurrence": 0}}
            for i in range(1, 11)
        ]
        self.review = {
            "record_id": "c" * 32, "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": self.chapter["translation_acceptance"]["source_sha256"],
            "translation_sha256": self.chapter["translation_acceptance"]["translation_sha256"],
            "quality_report": {"evidence_version": 2, "findings": self.findings},
        }
        self.status = {
            "state": "running", "phase": "claimed", "workflow_revision": "a" * 40,
            "model_calls_started": 0,
            "active": {"role": "corrector", "chapter": 26, "session_id": "test-claim"},
        }

    @staticmethod
    def reply(_role, _system, user):
        ids = json.loads(user)
        return json.dumps({
            "patches": [
                {"finding_id": fid, "operation": "replace",
                 "replacement": "fixed-" + fid.split("-")[1].zfill(2)}
                for fid in ids
            ],
            "glossary": [], "style_guide": [],
        })

    def run_once(self, *, response=None):
        def context(_engine, _slug, _chapter, _role, *, correction_findings):
            return ("corrector", json.dumps([f["id"] for f in correction_findings]), "fixture")
        with patch.object(worker, "ROOT", self.root), \
             patch.object(worker, "context", side_effect=context), \
             patch.object(worker, "role_contracts",
                          return_value=("pinned translator", ["AGENTS.md", "docs/TRANSLATOR.md"])), \
             patch.object(worker, "checkpoint") as cp, \
             patch.object(worker, "complete", side_effect=response or self.reply) as calls:
            output = worker.process_correction_batches(
                "f" * 32, self.slug, self.status, self.root, self.chapter, self.review)
        return output, calls.call_count, cp.call_count

    def test_ten_findings_are_staged_over_two_actions_without_partial_translation_mutation(self):
        self.assertEqual(worker.CORRECTION_BATCH_SIZE, 3)
        self.assertEqual(worker.CORRECTION_BATCH_CALLS_PER_CHECKPOINT, 2)
        first, calls, checkpoints = self.run_once()
        self.assertIsNone(first)
        self.assertEqual(calls, 2)
        self.assertEqual(checkpoints, 4)
        self.assertEqual(self.status["correction_batch_progress"]["completed"], 6)
        stage_path = worker.correction_stage_path(self.slug, self.chapter["unit_id"])
        # ROOT is patched only inside run_once: use the actual fixture location.
        stage_path = self.book / ".workflow/auto-correction-batches/chapter-000026.json"
        self.assertTrue(stage_path.is_file())
        self.assertEqual(len(json.loads(stage_path.read_text("utf-8"))["patches"]), 6)
        self.assertEqual((self.book / self.chapter["translation_path"]).read_bytes(),
                         self.original_translation)

        last, calls, checkpoints = self.run_once()
        self.assertIsNotNone(last)
        self.assertEqual(calls, 2)
        self.assertEqual(checkpoints, 4)
        translated, glossary, style = last
        self.assertEqual(translated,
                         " ".join(f"fixed-{i:02d}" for i in range(1, 11)))
        self.assertEqual((glossary, style), ([], []))
        # The caller (not the staging helper) owns atomic canonical acceptance.
        self.assertEqual((self.book / self.chapter["translation_path"]).read_bytes(),
                         self.original_translation)
        self.assertEqual(self.status["correction_batch_progress"]["completed"], 10)

    def test_partial_small_batch_is_accepted_and_remaining_finding_is_queued(self):
        def partial(role, system, user):
            first = json.loads(user)[0]
            return json.dumps({
                "patches": [{"finding_id": first, "operation": "replace",
                             "replacement": "new-" + first}],
                "glossary": [], "style_guide": [],
            })
        output, calls, _ = self.run_once(response=partial)
        self.assertIsNone(output)
        self.assertEqual(calls, 2)
        self.assertEqual(self.status["correction_batch_progress"]["completed"], 2)
        self.assertEqual((self.book / self.chapter["translation_path"]).read_bytes(),
                         self.original_translation)

    def test_stale_stage_review_identity_never_resumes(self):
        self.run_once()
        changed = {**self.review, "record_id": "d" * 32}
        with patch.object(worker, "ROOT", self.root):
            with self.assertRaisesRegex(worker.JobError, "stale or invalid"):
                worker.load_correction_stage(
                    self.slug, self.chapter, changed, self.status,
                    (self.book / self.chapter["source_path"]).read_text("utf-8"),
                    self.original_translation.decode("utf-8"), self.findings,
                )

    def test_stage_tampering_does_not_change_canonical_translation(self):
        self.run_once()
        stage_path = self.book / ".workflow/auto-correction-batches/chapter-000026.json"
        stage = json.loads(stage_path.read_text("utf-8"))
        stage["patches"][0]["replacement"] = "unsafe but schema-valid change"
        # Structural/hash validation guards review and target, not the literary
        # content of a staged proposal. Bad target identities must fail closed.
        stage["patches"][0]["target_anchor"]["quote"] = "nonexistent text"
        stage_path.write_text(json.dumps(stage), encoding="utf-8")
        with patch.object(worker, "ROOT", self.root):
            with self.assertRaisesRegex(worker.JobError, "staged correction"):
                worker.load_correction_stage(
                    self.slug, self.chapter, self.review, self.status,
                    (self.book / self.chapter["source_path"]).read_text("utf-8"),
                    self.original_translation.decode("utf-8"), self.findings,
                )
        self.assertEqual((self.book / self.chapter["translation_path"]).read_bytes(),
                         self.original_translation)

    def test_partial_response_requires_at_least_one_patch(self):
        payload = json.dumps({"patches": [], "glossary": [], "style_guide": []})
        with self.assertRaisesRegex(ModelError, "no actionable"):
            worker.parse_correction_patches(payload, self.findings[:3], allow_partial=True)

    def test_unexpected_finding_is_rejected_even_with_partial_mode(self):
        payload = json.dumps({"patches": [{
            "finding_id": "other-99", "operation": "replace", "replacement": "not allowed"
        }], "glossary": [], "style_guide": []})
        with self.assertRaisesRegex(ModelError, "unresolved finding"):
            worker.parse_correction_patches(payload, self.findings[:3], allow_partial=True)


class ActionQueueAdmissionTests(unittest.TestCase):
    def test_bounded_fair_selector_does_not_enqueue_all_books(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            intake = root / "intake" / "auto"
            status_dir = root / "automation" / "status"
            status_dir.mkdir(parents=True)
            for index in range(9):
                jid = f"{index + 1:032x}"
                slug = f"sample-{index}"
                request = intake / jid / "request.json"
                request.parent.mkdir(parents=True)
                request.write_text(json.dumps({
                    "job_id": jid, "source_sha256": "a" * 64,
                    "target_language": "ru",
                }), encoding="utf-8")
                # Reverse lexical order so admission fairness is observable.
                (status_dir / (jid + ".json")).write_text(json.dumps({
                    "state": "running", "book_slug": slug,
                    "updated_at": f"2026-11-12T{20-index:02d}:00:00+00:00",
                }), encoding="utf-8")
            with patch.object(worker, "ROOT", root):
                selected = worker.ready_jobs()
                self.assertEqual(len(selected), 6)
                self.assertEqual(selected[0]["job_id"], f"{9:032x}")
                self.assertEqual(selected[-1]["job_id"], f"{4:032x}")
                with self.assertRaises(worker.JobError):
                    worker.ready_jobs(limit=200)
                # Once the oldest book advances, the next wake rotates it.
                (status_dir / (f"{9:032x}.json")).write_text(json.dumps({
                    "state": "running", "book_slug": "sample-8",
                    "updated_at": "2026-11-12T23:59:59+00:00",
                }), encoding="utf-8")
                self.assertNotIn(f"{9:032x}",
                                 [job["job_id"] for job in worker.ready_jobs()])



if __name__ == "__main__":
    unittest.main()
