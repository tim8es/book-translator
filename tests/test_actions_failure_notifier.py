"""Tests for admin-only recovery alerts when GitHub Actions never creates jobs."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import actions_failure_notifier as notifier

NOW = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
JOB = "8d01a94492d8b2ae2a4c529f75bbefd6"


def run_data(run_id, conclusion="startup_failure", *, age=timedelta(minutes=10)):
    return {"id": run_id, "status": "completed", "conclusion": conclusion,
            "created_at": (NOW - age).isoformat(),
            "display_title": "Book translation " + JOB,
            "head_sha": "a" * 40,
            "html_url": f"https://github.com/example/repo/actions/runs/{run_id}"}


class AdminRecoveryTests(unittest.TestCase):
    def test_only_recent_prejob_failures_are_recovered(self):
        cases = [run_data(1), run_data(2, "action_required"),
                 run_data(3, "failure"), run_data(4, "cancelled"),
                 run_data(5, age=timedelta(hours=40)),
                 dict(run_data(6), status="queued")]
        self.assertEqual([x["id"] for x in notifier.recent_failures(cases, NOW)], [1, 2])

    def test_job_id_from_run_title_without_repository_checkout(self):
        self.assertEqual(notifier.extract_job_id(Path("/unavailable"), run_data(511)), JOB)

    def test_legacy_job_id_from_intake_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email",
                            "test@example.invalid"], check=True)
            path = root / "intake" / "auto" / JOB / "request.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"job_id":"' + JOB + '"}', encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "intake"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                          text=True).strip()
            payload = dict(run_data(511), display_title="Automatic book translation",
                           head_sha=sha)
            self.assertEqual(notifier.extract_job_id(root, payload), JOB)

    def test_callback_marks_recipient_as_admin_and_preserves_legacy_fields(self):
        requests = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return Response()
        with patch.object(notifier.urllib.request, "build_opener", return_value=Opener()):
            notifier.callback("https://n8n.example.test/hook", run_data(511), JOB)
        payload = json.loads(requests[0].data)
        self.assertEqual(payload["job_id"], JOB)
        self.assertEqual(payload["run_id"], "511")
        self.assertEqual(payload["conclusion"], "startup_failure")
        self.assertEqual(payload["recipient"], "admin")
        self.assertTrue(payload["admin_alert"])
        self.assertEqual(payload["event_id"], "github-actions-511")

    def test_durable_receipt_prevents_duplicate_notification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = run_data(511)
            # The integration-style main() uses the real clock, unlike the
            # deterministic filtering test above.
            event["created_at"] = (datetime.now(timezone.utc) -
                                   timedelta(minutes=10)).isoformat()
            env = {"GH_TOKEN": "fake", "GITHUB_REPOSITORY": "example/repo",
                   "N8N_DELIVERY_CALLBACK_URL": "https://n8n.example.test/hook"}
            with patch.object(notifier, "ROOT", root), \
                 patch.dict("os.environ", env), \
                 patch.object(notifier, "get_runs", return_value=[event]), \
                 patch.object(notifier, "callback") as send, \
                 patch.object(notifier, "git_ack"):
                with patch.object(notifier, "extract_job_id", return_value=JOB):
                    self.assertEqual(notifier.main(), 0)
                    self.assertEqual(notifier.main(), 0)
                send.assert_called_once()
            receipt = root / "automation" / "notifications" / "sent" / "_actions" / "511.json"
            self.assertTrue(receipt.is_file())
            self.assertEqual(json.loads(receipt.read_text())["recipient"], "admin")


if __name__ == "__main__":
    unittest.main()
