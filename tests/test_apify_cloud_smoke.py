"""Offline tests for the authenticated Apify cloud-smoke entrypoint."""
import io
import json
import os
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import apify_cloud_smoke as smoke


class CloudSmokeTests(unittest.TestCase):
    def test_missing_token_exits_without_network(self):
        with patch.dict(os.environ, {"APIFY_TOKEN": ""}):
            with patch.object(smoke, "api_json") as api:
                self.assertEqual(smoke.main(), 2)
                api.assert_not_called()

    def test_private_actor_is_required(self):
        rows = {"data": {"items": [{"name": smoke.ACTOR_NAME, "id": "actor-id"}]}}
        details = {"data": {"isPublic": True}}
        with patch.object(smoke, "api_json", side_effect=[rows, details]):
            with self.assertRaisesRegex(smoke.SmokeError, "not confirmed as private"):
                smoke.find_private_actor("synthetic-token")

    def test_locates_private_actor(self):
        rows = {"data": {"items": [{"name": smoke.ACTOR_NAME, "id": "actor-id"}]}}
        details = {"data": {"isPublic": False}}
        with patch.object(smoke, "api_json", side_effect=[rows, details]):
            self.assertEqual(smoke.find_private_actor("synthetic-token"), "actor-id")

    def test_two_chapter_artifacts_are_validated(self):
        summary = {
            "status": "complete",
            "chapters": 2,
            "outputKeys": ["OUTPUT_EPUB", "OUTPUT_MARKDOWN"],
            "sourceWords": 140,
            "llmCostUsd": 0.04,
            "usage": [{"model": "translation-model", "role": "translation",
                       "inputTokens": 120, "outputTokens": 200, "costUsd": 0.04}],
        }
        key_names = ("CHAPTER_000001", "CHAPTER_000002", "OUTPUT_EPUB", "OUTPUT_MARKDOWN", "SUMMARY")
        pages = [
            summary,
            {"data": {"items": [{"key": name} for name in key_names]}},
        ]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as epub:
            epub.writestr("mimetype", b"application/epub+zip")
            epub.writestr("META-INF/container.xml", "<container />")
        outputs = [buffer.getvalue(), "Переведено".encode("utf-8")]
        with (
            patch.object(smoke, "api_json", side_effect=pages),
            patch.object(smoke, "api_request", side_effect=outputs),
            redirect_stdout(io.StringIO()) as output,
        ):
            smoke.check_artifacts(
                "synthetic-token",
                "synthetic-run",
                {"status": "SUCCEEDED", "defaultKeyValueStoreId": "store", "usageTotalUsd": 0.001},
            )
        status = json.loads(output.getvalue())
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(status["chapters"], 2)
        self.assertEqual(status["ownerLlmUsd"], 0.04)


if __name__ == "__main__":
    unittest.main()
