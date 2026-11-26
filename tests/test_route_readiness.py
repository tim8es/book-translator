import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import route_readiness as readiness


class RouteReadinessTests(unittest.TestCase):
    def env(self):
        return {"OMNIROUTE_" + role + "_MODEL": "primary"
                for role in readiness.PRIMARY_ROLES}

    def test_missing_and_same_alias_never_calls(self):
        env = self.env()
        env["OMNIROUTE_TRANSLATOR_FALLBACK_MODEL"] = " primary "
        complete = Mock()
        report = {}
        readiness.probe(env, complete, report)
        complete.assert_not_called()
        self.assertEqual(report["distinct_primary_routes"], 1)
        self.assertEqual(report["roles"]["translator"]["status"], "same_as_primary")
        self.assertEqual(report["roles"]["reviewer"]["status"], "missing")
        self.assertFalse(report["all_fallbacks_ready"])

    def test_shared_other_primary_alias_is_not_independent(self):
        env = self.env()
        env["OMNIROUTE_REVIEWER_MODEL"] = "other"
        env["OMNIROUTE_TRANSLATOR_FALLBACK_MODEL"] = "other"
        complete = Mock()
        report = {}
        readiness.probe(env, complete, report)
        complete.assert_not_called()
        self.assertEqual(report["roles"]["translator"]["status"], "shared_primary_alias")

    def test_distinct_aliases_make_three_bounded_calls(self):
        env = self.env()
        for role in readiness.ROLES:
            env["OMNIROUTE_" + role + "_FALLBACK_MODEL"] = "hidden-backup-" + role
        complete = Mock(return_value='{"ready":true}')
        report = {}
        readiness.probe(env, complete, report)
        self.assertEqual(report["model_calls_started"], 3)
        self.assertEqual(complete.call_count, 3)
        self.assertTrue(report["all_fallbacks_ready"])
        self.assertFalse(report["independent_fallback_provider_verified"])
        self.assertNotIn("hidden-backup", json.dumps(report))

    def test_provider_error_and_invalid_json_fail_without_leaking(self):
        env = self.env()
        for role in readiness.ROLES:
            env["OMNIROUTE_" + role + "_FALLBACK_MODEL"] = "hidden-backup"
        complete = Mock(side_effect=[RuntimeError("secret https://example.invalid"),
                                     '{"ready":1}', '{"ready":true}'])
        report = {}
        readiness.probe(env, complete, report)
        self.assertFalse(report["all_fallbacks_ready"])
        self.assertEqual(report["roles"]["translator"]["status"], "probe_failed")
        self.assertEqual(report["roles"]["reviewer"]["status"], "invalid_response")
        self.assertNotIn("example.invalid", json.dumps(report))
