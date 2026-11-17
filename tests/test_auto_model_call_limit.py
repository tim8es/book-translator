"""Regression coverage for the configured provider call cap."""
import os
import json
import tempfile
from copy import deepcopy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import book_runner as worker


class ModelCallLimitTests(unittest.TestCase):
    def test_configured_cap_accepts_1500(self):
        for value in ("1", "400", "1500"):
            with self.subTest(value=value), patch.dict(os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": value}):
                self.assertEqual(worker.model_call_limit(), int(value))

    def test_configured_cap_rejects_over_limit_and_invalid_values(self):
        for value in ("0", "1501", "-1", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": value}):
                with self.assertRaisesRegex(worker.JobError, "1 to 1500"):
                    worker.model_call_limit()


class ModelBudgetResumeTests(unittest.TestCase):
    def blocked_status(self):
        return {"state": "blocked", "phase": "model-call-limit-reached",
                "active": None, "book_slug": "synthetic-book",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "model_calls_started": 400, "correction_rounds": {"chapter-000001": 2},
                "recoveries": [{"action": "older-recovery"}],
                "last_error": "configured model invocation cap reached"}

    def test_resume_signal_requires_unused_budget_and_fresh_auto_mode(self):
        status = self.blocked_status()
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "books/synthetic-book/.workflow/auto-control.json"
            control.parent.mkdir(parents=True)
            for mode, updated, cap, expected in (
                ("auto", "2026-01-02T00:00:00+00:00", "1500", True),
                ("auto", "2026-01-02T00:00:00+00:00", "400", False),
                ("auto", "2026-01-02T00:00:00+00:00", "399", False),
                ("chatops", "2026-01-02T00:00:00+00:00", "1500", False),
                ("auto", "2026-01-01T00:00:00+00:00", "1500", False),
            ):
                with self.subTest(mode=mode, updated=updated, cap=cap):
                    control.write_text(json.dumps({"schema_version": 1, "mode": mode,
                                                   "updated_at": updated}), encoding="utf-8")
                    with patch.object(worker, "ROOT", Path(root)), patch.dict(
                            os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": cap}):
                        self.assertEqual(worker.auto_resume_requested(status), expected)

    def test_approval_preserves_cumulative_calls_and_review_history(self):
        status = self.blocked_status()
        with patch.dict(os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": "1500"}):
            worker.approve_blocked_resume(status)
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["model_calls_started"], 400)
        self.assertEqual(status["correction_rounds"], {"chapter-000001": 2})
        self.assertEqual(status["recoveries"][0], {"action": "older-recovery"})
        self.assertEqual(status["recoveries"][-1]["action"], "resume-model-call-limit-reached")
        self.assertEqual(status["recoveries"][-1]["model_calls_started"], 400)
        self.assertEqual(status["recoveries"][-1]["model_call_limit"], 1500)

    def test_approval_fails_closed_without_valid_unused_budget(self):
        for count, active, cap in ((400, None, "400"), (401, None, "400"),
                                   (-1, None, "1500"), (True, None, "1500"),
                                   ("400", None, "1500"), (None, None, "1500"),
                                   (400, {"role": "translator"}, "1500")):
            with self.subTest(count=count, active=active, cap=cap):
                status = self.blocked_status()
                status.update(model_calls_started=count, active=active)
                before = deepcopy(status)
                with patch.dict(os.environ, {"OMNIROUTE_MODEL_CALL_LIMIT": cap}):
                    with self.assertRaises(worker.JobError):
                        worker.approve_blocked_resume(status)
                self.assertEqual(status, before)
