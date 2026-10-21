"""Strict apples-to-apples efficiency benchmarking; no unmeasured claims."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.session_benchmark import BenchmarkError, evaluate, evaluate_file


def fixture():
    def sample(label, sessions, seconds, major=1):
        return {
            "run_id": label, "sessions": sessions,
            "unit_ids": ["chapter-000001", "chapter-000002"],
            "reviewed_unit_ids": ["chapter-000001", "chapter-000002"],
            "source_words": 800, "elapsed_seconds": seconds,
            "github_requests": 30 if sessions == 1 else 22,
            "manual_interventions": 8 if sessions == 1 else 3,
            "major_findings": major, "critical_findings": 0,
        }
    return {
        "schema_version": 1, "dataset_id": "synthetic-fixture-for-tests",
        "evaluation_protocol": "same-independent-review-protocol",
        "runs": [sample("baseline", 1, 4000), sample("parallel", 3, 1800)],
    }


class BenchmarkTests(unittest.TestCase):
    def test_reproducible_rates_with_clear_quality_boundary(self):
        report = evaluate(fixture())
        self.assertEqual(report["summary"][0]["samples"], 1)
        self.assertGreater(report["comparisons"][0]["throughput_speedup"], 2)
        self.assertTrue(report["comparisons"][0]["meets_provisional_2x_target"])
        self.assertIn("not automatic timing", report["qualification"])

    def test_quality_regression_disqualifies_speedup(self):
        data = fixture()
        data["runs"][1]["major_findings"] = 2
        result = evaluate(data)
        self.assertFalse(result["comparisons"][0]["meets_provisional_2x_target"])

    def test_partial_review_or_different_corpus_rejected(self):
        data = fixture()
        data["runs"][1]["reviewed_unit_ids"] = ["chapter-000001"]
        with self.assertRaisesRegex(BenchmarkError, "independently reviewed"):
            evaluate(data)
        data = fixture()
        data["runs"][1]["source_words"] = 801
        with self.assertRaisesRegex(BenchmarkError, "identical"):
            evaluate(data)

    def test_same_sessions_without_single_chat_baseline_rejected(self):
        data = fixture()
        data["runs"][0]["sessions"] = 2
        with self.assertRaisesRegex(BenchmarkError, "single-chat baseline"):
            evaluate(data)

    def test_repeated_runs_use_median_and_file_is_read_only(self):
        data = fixture()
        second = copy.deepcopy(data["runs"][0])
        second["run_id"] = "baseline-2"
        second["elapsed_seconds"] = 5000
        data["runs"].append(second)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "run.json"
            raw = json.dumps(data, sort_keys=True)
            path.write_text(raw, encoding="utf-8")
            out = evaluate_file(path)
            self.assertEqual(out["summary"][0]["samples"], 2)
            self.assertEqual(path.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
