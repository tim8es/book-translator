"""Offline tests for the bounded usage qualification profile."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import usage_qualification as usage
from omniroute import CompletionText


class UsageQualificationTests(unittest.TestCase):
    def test_all_roles_use_opt_in_transport_and_restore_environment(self):
        calls = []
        def complete(role, system, user):
            calls.append(role)
            self.assertEqual(os.environ["BOOK_TRANSLATOR_LLM_BACKEND"], "pi")
            self.assertEqual(os.environ["BOOK_TRANSLATOR_PI_USAGE_EVENTS"], "on")
            return CompletionText('{"ready":true}', {"prompt_tokens": 20,
                "completion_tokens": 4, "total_tokens": 24, "provider": "secret-sentinel"})
        report = {}
        with patch.dict(os.environ, {"BOOK_TRANSLATOR_PI_USAGE_EVENTS": "off"}):
            usage.probe(complete, report)
            self.assertEqual(os.environ["BOOK_TRANSLATOR_PI_USAGE_EVENTS"], "off")
        self.assertEqual(calls, list(usage.ROLES))
        self.assertEqual(report["model_calls_started"], 4)
        self.assertEqual(report["qualification"], "passed")
        self.assertIsNone(report["billed_cost"])
        self.assertNotIn("secret-sentinel", str(report))

    def test_missing_usage_is_not_a_pass_even_when_transport_works(self):
        report = {}
        usage.probe(lambda *args: CompletionText('{"ready":true}', {}), report)
        self.assertTrue(report["all_transports_ready"])
        self.assertFalse(report["token_usage_complete"])
        self.assertEqual(report["qualification"], "failed")
        self.assertIsNone(report["roles"]["reviewer"]["token_usage"])

    def test_no_retry_and_no_sensitive_failure_text(self):
        calls = []
        def complete(role, *args):
            calls.append(role)
            if role == "TRANSLATOR":
                raise RuntimeError("secret-sentinel")
            return CompletionText('{"ready":1}', {"total_tokens": 24})
        report = {}
        usage.probe(complete, report)
        self.assertEqual(calls, list(usage.ROLES))
        self.assertFalse(report["all_transports_ready"])
        self.assertEqual(report["qualification"], "failed")
        self.assertNotIn("secret-sentinel", str(report))
