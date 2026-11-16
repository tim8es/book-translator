"""Synthetic first-pass prompt and review-stagnation regression tests."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as auto
import review_convergence as convergence


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record(index: int, score: int, *, source: str = "s", outcome: str = "CORRECTIONS_REQUIRED") -> dict:
    # Choose major/minor combinations whose severity score is explicit.
    findings = [{"id": f"f-{j}", "resolved": False,
                 "category": "meaning", "severity": "major" if j < score // 3 else "minor"}
                for j in range(score // 3 + score % 3)]
    return {
        "record_id": f"{index:032x}",
        "unit_id": "chapter-000001",
        "source_sha256": digest(source),
        "translation_sha256": digest("translation-" + str(index)),
        "outcome": outcome,
        "quality_report": {"evidence_version": 2, "findings": findings},
    }


class ConvergenceDiagnosticsTests(unittest.TestCase):
    def fixture(self, scores=(3, 4, 5)):
        records = [record(i, score) for i, score in enumerate(scores, 1)]
        ledger = {"records": records}
        chapter = {
            "number": 1, "unit_id": "chapter-000001",
            "translation_acceptance": {
                "source_sha256": digest("s"),
                "translation_sha256": records[-1]["translation_sha256"],
            },
        }
        return ledger, chapter

    def test_stalling_after_two_unproductive_changes(self):
        ledger, chapter = self.fixture((3, 4, 5))
        result = convergence.stalled_rejected_reviews(ledger, chapter)
        self.assertEqual(result["severity_scores"], [3, 4, 5])
        self.assertEqual(result["review_record_ids"], [
            f"{i:032x}" for i in (1, 2, 3)])
        self.assertEqual(result["action"], "operator-attention-no-pass")

    def test_stable_problem_size_stalls_without_declaring_pass(self):
        ledger, chapter = self.fixture((3, 3, 3))
        self.assertIsNotNone(convergence.stalled_rejected_reviews(ledger, chapter))

    def test_improvement_resets_stagnation_detection(self):
        ledger, chapter = self.fixture((5, 3, 4))
        self.assertIsNone(convergence.stalled_rejected_reviews(ledger, chapter))

    def test_refuse_stale_mixed_source_or_prior_pass(self):
        ledger, chapter = self.fixture()
        chapter["translation_acceptance"]["translation_sha256"] = digest("another")
        self.assertIsNone(convergence.stalled_rejected_reviews(ledger, chapter))
        ledger, chapter = self.fixture()
        ledger["records"][1] = record(2, 4, source="different")
        self.assertIsNone(convergence.stalled_rejected_reviews(ledger, chapter))
        ledger, chapter = self.fixture()
        ledger["records"][1]["outcome"] = "PASS"
        self.assertIsNone(convergence.stalled_rejected_reviews(ledger, chapter))

    def test_refuse_duplicate_translation_or_unanchored_report(self):
        ledger, chapter = self.fixture()
        ledger["records"][1]["translation_sha256"] = ledger["records"][0]["translation_sha256"]
        self.assertIsNone(convergence.stalled_rejected_reviews(ledger, chapter))
        ledger, chapter = self.fixture()
        ledger["records"][1]["quality_report"]["evidence_version"] = 1
        self.assertIsNone(convergence.stalled_rejected_reviews(ledger, chapter))

    def test_canonical_defer_requires_current_hash_and_exact_review(self):
        ledger, chapter = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            engine = Path(folder)
            (engine / "workflow").mkdir()
            (engine / "workflow" / "attention.py").touch()
            status = {}
            with patch.object(auto, "pinned_scripts_dir", return_value=engine), \
                 patch.object(auto, "cli") as cli:
                self.assertTrue(auto.defer_stalled_review(
                    "synthetic", status, ledger, chapter))
            cli.assert_called_once_with(
                "defer-review", "synthetic", "1", "--reason", "operator-attention")
            self.assertEqual(status["last_review_stagnation"]["severity_scores"],
                             [3, 4, 5])
            self.assertNotIn("deferred_review_units", status)

    def test_legacy_defer_no_pass_and_operator_override(self):
        ledger, chapter = self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            engine = Path(folder)
            with patch.object(auto, "pinned_scripts_dir", return_value=engine), \
                 patch.object(auto, "cli") as cli:
                status = {"operator_review_limits": {"chapter-000001": 7}}
                self.assertFalse(auto.defer_stalled_review(
                    "synthetic", status, ledger, chapter))
                self.assertNotIn("deferred_review_units", status)
                status = {}
                self.assertTrue(auto.defer_stalled_review(
                    "synthetic", status, ledger, chapter))
                self.assertEqual(status["deferred_review_units"],
                                 ["chapter-000001"])
                cli.assert_not_called()


class FirstPassPromptTests(unittest.TestCase):
    def test_translator_and_reviewer_require_explicit_quality_sweep(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            book = root / "books" / "synthetic"
            (book / "extracted").mkdir(parents=True)
            (book / "translated").mkdir()
            (book / "extracted" / "1.md").write_text(
                "He might visit tomorrow. She did not know.", "utf-8")
            (book / "translated" / "1.md").write_text(
                "Он может приехать завтра. Она не знала.", "utf-8")
            (book / "glossary.md").write_text("", "utf-8")
            (book / "style-guide.md").write_text("", "utf-8")
            (book / "metadata.json").write_text(json.dumps({
                "title": "Synthetic", "author": "Test Author",
                "source_language": "en", "target_language": "ru",
            }), "utf-8")
            chapter = {"unit_id": "chapter-000001", "number": 1,
                       "status": "translated", "source_path": "extracted/1.md",
                       "translation_path": "translated/1.md"}
            with patch.object(auto, "ROOT", root), \
                 patch.object(auto, "chapter_data",
                              return_value=({}, {"records": []}, [chapter])), \
                 patch.object(auto, "role_contracts",
                              return_value=("Pinned role instruction", ["AGENTS.md"])):
                translator, _, _ = auto.context(root, "synthetic", chapter, "translator")
                reviewer, _, _ = auto.context(root, "synthetic", chapter, "reviewer")
                self.assertIn("SINGLE-PASS FIDELITY CHECK", translator)
                self.assertIn("modal verbs", translator)
                self.assertIn("USEFUL INDEPENDENT REVIEW", reviewer)
                self.assertIn("complete supplied source sequentially", reviewer)
                self.assertNotIn("SINGLE-PASS FIDELITY CHECK", reviewer)


if __name__ == "__main__":
    unittest.main()
