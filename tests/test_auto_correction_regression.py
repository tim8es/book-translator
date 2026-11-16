"""Synthetic regression tests for pre-acceptance correction safety.

No copyrighted text or external model credentials are needed.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import correction_regression as guard
import book_runner as auto


class PureCorrectionRegressionTests(unittest.TestCase):
    def test_long_repeated_clause_introduced_by_patch_is_rejected(self):
        before = "Он медленно закрыл окно, а затем взглянул на дверь."
        after = before + " Он медленно закрыл окно, а затем взглянул на дверь."
        issues = guard.new_repetitions(before, after)
        self.assertTrue(issues)
        self.assertTrue(all(set(item) == {
            "tokens", "previous", "candidate", "adjacent"} for item in issues))
        self.assertNotIn("окно", repr(issues))
        with self.assertRaises(guard.CorrectionRegression) as error:
            guard.require_no_new_repetitions(before, after)
        self.assertEqual(error.exception.code, "new-duplicated-phrase")

    def test_short_immediate_echo_is_rejected(self):
        with self.assertRaises(guard.CorrectionRegression):
            guard.require_no_new_repetitions(
                "Море было тихим.",
                "Море было тихим. Море было тихим.")

    def test_old_repetitions_and_single_word_emphasis_are_not_new_errors(self):
        original = "Там было тихо. Там было тихо.\n\nОн произнёс: «Нет, нет!»"
        guard.require_no_new_repetitions(
            original, original.replace("произнёс", "ответил"))
        guard.require_no_new_repetitions("Да.", "Да, да.")

    def test_safe_edit_and_removing_duplicate_are_not_rejected(self):
        initial = "Я вижу мост и реку. Я вижу мост и реку."
        guard.require_no_new_repetitions(initial, "Я вижу мост и реку.")
        guard.require_no_new_repetitions("Река шумела.", "Река слегка шумела.")

    def test_stable_patch_fingerprint_is_content_free(self):
        patch_value = {"finding_id": "x", "replacement": "секретный фрагмент"}
        digest = guard.patch_fingerprint(patch_value)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, guard.patch_fingerprint(dict(reversed(
            list(patch_value.items())))))
        self.assertNotIn("секретный", digest)

    def test_classification_is_diagnostic_not_a_pass_override(self):
        findings = [
            {"severity": "minor", "category": "style"},
            {"severity": "minor", "category": "register"},
            {"severity": "minor", "category": "meaning"},
            {"severity": "major", "category": "style"},
            {"severity": "minor", "category": "omission"},
            {"severity": "minor", "category": "unknown"},
        ]
        self.assertEqual(guard.triage_counts(findings), {
            "required": 3, "adjudicate": 2, "review_required": 1})


class CorrectionBatchGuardTests(unittest.TestCase):
    JOB = "a" * 32

    def fixture(self, folder):
        root = Path(folder)
        book = root / "books" / "synthetic"
        (book / "extracted").mkdir(parents=True)
        (book / "translated").mkdir()
        (book / "glossary.md").write_text("", encoding="utf-8")
        (book / "style-guide.md").write_text("", encoding="utf-8")
        text = "Первое предложение. Море было тихим. Последнее предложение."
        (book / "extracted" / "unit.md").write_text(text, encoding="utf-8")
        (book / "translated" / "unit.md").write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "source_path": "extracted/unit.md",
            "translation_path": "translated/unit.md",
            "translation_acceptance": {
                "source_sha256": digest, "translation_sha256": digest,
            },
        }
        findings = [
            {"id": "fix-1", "resolved": False, "severity": "major",
             "category": "meaning"},
            {"id": "fix-2", "resolved": False, "severity": "minor",
             "category": "style"},
        ]
        record = {
            "record_id": "review-1", "outcome": "CORRECTIONS_REQUIRED",
            "source_sha256": digest, "translation_sha256": digest,
            "quality_report": {"evidence_version": 2, "findings": findings},
        }
        status = {
            "workflow_revision": "b" * 40,
            "active": {"session_id": "test"},
            "model_calls_started": 0,
        }
        stage = {"patches": [], "glossary": [], "style_guide": []}
        stage_path = book / ".workflow" / "auto-correction-batches" / "chapter-000001.json"
        safe = {
            "finding_id": "fix-1", "operation": "replace",
            "target_anchor": {"quote": "Первое предложение", "occurrence": 0},
            "replacement": "Начальное предложение",
        }
        unsafe = {
            "finding_id": "fix-2", "operation": "replace",
            "target_anchor": {"quote": "Море было тихим", "occurrence": 0},
            "replacement": "Море было тихим. Море было тихим",
        }
        return root, chapter, record, status, stage, stage_path, safe, unsafe

    def process(self, fixture, patches):
        root, chapter, record, status, stage, stage_path, *_ = fixture
        with patch.object(auto, "ROOT", root), \
             patch.object(auto, "load_correction_stage", return_value=(
                 stage_path, stage)), \
             patch.object(auto, "context", return_value=("sys", "user", "")), \
             patch.object(auto, "role_contracts", return_value=("sys", ["AGENTS.md"])), \
             patch.object(auto, "checkpoint"), \
             patch.object(auto, "complete_for_job", return_value="{}"), \
             patch.object(auto, "parse_correction_patches",
                          return_value=(patches, [], [])):
            return auto.process_correction_batches(
                self.JOB, "synthetic", status, root, chapter, record)

    def test_unsafe_only_does_not_write_artifact_or_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(folder)
            root, chapter, _, status, _, stage_path, _, unsafe = fixture
            artifact = root / "books" / "synthetic" / chapter["translation_path"]
            original = artifact.read_bytes()
            with self.assertRaisesRegex(auto.ModelError, "duplicated text"):
                self.process(fixture, [unsafe])
            self.assertEqual(artifact.read_bytes(), original)
            self.assertFalse(stage_path.exists())
            self.assertEqual(
                status["correction_regression_rejections"][0]["finding_id"],
                "fix-2")
            self.assertEqual(status["correction_finding_triage"]["counts"],
                             {"required": 1, "adjudicate": 1, "review_required": 0})

    def test_safe_partial_patch_survives_and_regression_is_deferred(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(folder)
            root, chapter, _, status, _, stage_path, safe, unsafe = fixture
            result = self.process(fixture, [safe, unsafe])
            self.assertIn("Начальное предложение", result[0])
            self.assertEqual(stage_path.is_file(), True)
            staged = json.loads(stage_path.read_text("utf-8"))
            self.assertEqual([p["finding_id"] for p in staged["patches"]],
                             ["fix-1"])
            self.assertEqual(status["correction_regression_deferred"][
                "unapplied_finding_ids"], ["fix-2"])
            # The canonical translation must not be changed before acceptance.
            artifact = root / "books" / "synthetic" / chapter["translation_path"]
            self.assertNotIn("Начальное предложение", artifact.read_text("utf-8"))

    def test_same_rejected_patch_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = self.fixture(folder)
            root, chapter, record, status, _, stage_path, _, unsafe = fixture
            status["correction_regression_rejections"] = [{
                "unit_id": chapter["unit_id"],
                "review_record_id": record["record_id"],
                "finding_id": unsafe["finding_id"],
                "patch_sha256": guard.patch_fingerprint(unsafe),
                "reason": "new-duplicated-phrase",
                "at": "2026-11-16T00:00:00Z",
            }]
            with self.assertRaisesRegex(auto.ModelError, "repeated an unchanged"):
                self.process(fixture, [unsafe])
            self.assertFalse(stage_path.exists())


if __name__ == "__main__":
    unittest.main()
