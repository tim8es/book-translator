"""Telemetry is independently recorded by Python without changing workflow outputs."""

import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import book
from workflow.github_api import GitHubRestClient
from workflow.telemetry import ENVIRONMENT_VARIABLE, emit


class FakeOpener:
    def open(self, request, timeout=None):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return b'{"hello": "world"}'
        return Response()


class RuntimeTelemetryTests(unittest.TestCase):
    def test_disabled_logging_does_not_write(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "events.jsonl"
            with patch.dict(os.environ, {ENVIRONMENT_VARIABLE: ""}):
                emit("cli", command="resume", elapsed_seconds=0.01)
            self.assertFalse(target.exists())

    def test_cli_and_github_requests_are_recorded_without_sensitive_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "events.jsonl"
            with patch.dict(os.environ, {ENVIRONMENT_VARIABLE: str(target)}):
                with patch.object(book, "build_parser") as parser:
                    parser.return_value.parse_args.return_value = Namespace(
                        command="resume", func=lambda _: 0
                    )
                    self.assertEqual(book.main(["resume"]), 0)
                client = GitHubRestClient("secret-token", opener=FakeOpener())
                self.assertEqual(
                    client._request_json("GET", "/repos/private/secret", query={"ref": "private"}),
                    {"hello": "world"},
                )
            events = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["kind"] for event in events], ["cli", "github_request"])
            self.assertEqual(events[0]["command"], "resume")
            self.assertEqual(events[0]["exit_code"], 0)
            self.assertEqual(events[1]["method"], "GET")
            self.assertEqual(events[1]["outcome"], "ok")
            self.assertTrue(all(event["elapsed_seconds"] >= 0 for event in events))
            self.assertNotIn("secret-token", target.read_text(encoding="utf-8"))
            self.assertNotIn("/repos/private/secret", target.read_text(encoding="utf-8"))

    def test_cli_failure_is_measured_without_changing_return_code(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "events.jsonl"
            with patch.dict(os.environ, {ENVIRONMENT_VARIABLE: str(target)}):
                with patch.object(book, "build_parser") as parser:
                    parser.return_value.parse_args.return_value = Namespace(
                        command="validate", func=lambda _: 1
                    )
                    self.assertEqual(book.main(["validate"]), 1)
            result = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["command"], "validate")


if __name__ == "__main__":
    unittest.main()
