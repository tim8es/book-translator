"""Measured backend and HTTP transport retain original failure/CAS semantics."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from workflow.filesystem import FilesystemStorage
from workflow.github_api import GitHubApiError, GitHubRestClient
from workflow.runtime_metrics import MeasuredStorage, RuntimeMetrics
from workflow.storage import StorageVersionConflict


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(response)


class RuntimeMetricsTests(unittest.TestCase):
    def test_storage_records_success_and_rejection_without_extra_backend_work(self):
        with tempfile.TemporaryDirectory() as temp:
            metrics = RuntimeMetrics()
            backend = FilesystemStorage(Path(temp))
            storage = MeasuredStorage(backend, metrics)
            original = storage.create_if_absent("progress.json", b"initial")
            self.assertEqual(storage.read("progress.json").content, b"initial")
            updated = storage.write_if_version("progress.json", b"new", original)
            with self.assertRaises(StorageVersionConflict):
                storage.write_if_version("progress.json", b"bad", original)
            self.assertEqual(storage.list(), ["progress.json"])
            storage.delete_if_version("progress.json", updated)
            self.assertFalse((Path(temp) / "progress.json").exists())
            stats = metrics.snapshot()
            self.assertEqual(stats["storage.create"]["calls"], 1)
            self.assertEqual(stats["storage.create"]["bytes_written"], 7)
            self.assertEqual(stats["storage.read"]["bytes_read"], 7)
            self.assertEqual(stats["storage.update"]["calls"], 2)
            self.assertEqual(stats["storage.update"]["failures"], 1)
            self.assertEqual(stats["storage.update"]["bytes_written"], 3)
            self.assertEqual(stats["storage.delete"]["calls"], 1)
            self.assertEqual(stats["storage.list"]["calls"], 1)
            self.assertGreaterEqual(stats["storage.read"]["elapsed_ns"], 0)
            self.assertEqual(json.loads(json.dumps(stats)), stats)

    def test_metrics_snapshot_does_not_share_mutable_counters(self):
        metrics = RuntimeMetrics(clock_ns=iter([10, 17, 20, 25]).__next__)
        with metrics.measure("operation"):
            metrics.add_bytes("operation", read=5)
        copy = metrics.snapshot()
        copy["operation"]["calls"] = 99
        with metrics.measure("operation"):
            pass
        self.assertEqual(metrics.snapshot()["operation"], {
            "calls": 2, "failures": 0, "elapsed_ns": 12,
            "bytes_read": 5, "bytes_written": 0,
        })

    def test_real_http_requests_are_measured_without_exposing_url_or_token(self):
        payload = b'{"type":"file","sha":"sha1","encoding":"base64","content":"YQ=="}'
        opener = FakeOpener(payload)
        metrics = RuntimeMetrics()
        client = GitHubRestClient("private-secret", opener=opener, metrics=metrics)
        self.assertEqual(client.get_file("owner/repo", "file.txt", "main").content, b"a")
        self.assertEqual(len(opener.requests), 1)
        measured = metrics.snapshot()
        self.assertEqual(measured["github.http.GET"]["calls"], 1)
        self.assertEqual(measured["github.http.GET"]["bytes_read"], len(payload))
        self.assertEqual(measured["github.http.GET"]["failures"], 0)
        self.assertNotIn("private-secret", json.dumps(measured))
        self.assertNotIn("file.txt", json.dumps(measured))

    def test_failed_http_request_counts_once_and_remains_fail_closed(self):
        opener = FakeOpener(HTTPError("https://api.github.com", 403, "Forbidden", {}, None))
        metrics = RuntimeMetrics()
        client = GitHubRestClient(None, opener=opener, metrics=metrics)
        with self.assertRaises(GitHubApiError):
            client.get_file("owner/repo", "locked.txt", "main")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(metrics.snapshot()["github.http.GET"]["failures"], 1)


if __name__ == "__main__":
    unittest.main()
