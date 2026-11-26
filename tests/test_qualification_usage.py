"""Cost evidence never treats missing provider usage as free calls."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import execution_metrics as metrics


class QualificationUsageTests(unittest.TestCase):
    def metric(self, status, role="translator", **fields):
        metrics.provider(status, {"role": role, "outcome": "response", **fields})

    def test_complete_observations_including_explicit_zero(self):
        status = {"model_calls_started": 2}
        self.metric(status, prompt_tokens=12, completion_tokens=0, total_tokens=12)
        self.metric(status, "reviewer", prompt_tokens=10, completion_tokens=3, total_tokens=13)
        report = metrics.qualification_usage(status)
        self.assertTrue(report["token_usage_complete"])
        self.assertEqual(report["tokens_if_complete"], {
            "prompt_tokens": 22, "completion_tokens": 3, "total_tokens": 25})
        self.assertIsNone(report["billed_cost"])

    def test_missing_or_partial_usage_is_not_zero(self):
        status = {"model_calls_started": 2}
        self.metric(status, prompt_tokens=12, completion_tokens=3, total_tokens=15)
        self.metric(status)
        report = metrics.qualification_usage(status)
        self.assertTrue(report["all_reserved_calls_recorded"])
        self.assertFalse(report["token_usage_complete"])
        self.assertEqual(report["roles"]["translator"]["token_observations"]["total_tokens"], 1)
        self.assertIsNone(report["tokens_if_complete"]["total_tokens"])

    def test_unrecorded_reservation(self):
        status = {"model_calls_started": 2}
        self.metric(status, prompt_tokens=1, completion_tokens=1, total_tokens=2)
        report = metrics.qualification_usage(status)
        self.assertFalse(report["all_reserved_calls_recorded"])
        self.assertFalse(report["token_usage_complete"])

    def test_legacy_sums_do_not_establish_observation_coverage(self):
        status = {"model_calls_started": 1, "execution_metrics": {"api": {
            "translator": {"completed_calls": 1, "total_tokens": 99,
                           "provider_model": "private-sentinel"}}}}
        report = metrics.qualification_usage(status)
        self.assertFalse(report["token_usage_complete"])
        self.assertIsNone(report["tokens_if_complete"]["total_tokens"])
        self.assertNotIn("private-sentinel", str(report))

    def test_empty_run_is_not_billing_evidence(self):
        report = metrics.qualification_usage({})
        self.assertFalse(report["token_usage_complete"])
        self.assertIsNone(report["billed_cost"])


if __name__ == "__main__":
    unittest.main()
