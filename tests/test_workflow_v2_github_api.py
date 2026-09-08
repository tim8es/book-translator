import base64
import io
import json
import unittest
from urllib.error import HTTPError, URLError

from workflow_v2.github_api import GitHubApiError

try:
    from workflow_v2.github_api import GitHubRestClient
except ImportError:
    GitHubRestClient = None


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(response)


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class WorkflowV2GitHubRestClientTests(unittest.TestCase):
    def require_api(self):
        self.assertIsNotNone(GitHubRestClient, "GitHubRestClient is not implemented")

    def client(self, *responses, token="secret-token"):
        self.require_api()
        opener = FakeOpener(*responses)
        return GitHubRestClient(token, opener=opener), opener

    def test_get_file_uses_contents_then_blob_and_returns_exact_binary(self):
        payload = b"binary\x00payload\xff"
        client, opener = self.client(
            encoded({"type": "file", "sha": "blob-sha"}),
            encoded({"encoding": "base64", "content": base64.b64encode(payload).decode("ascii")}),
        )

        result = client.get_file("owner/repo", "books/a b/state.bin", "feature/test branch")

        self.assertEqual(result.path, "books/a b/state.bin")
        self.assertEqual(result.blob_sha, "blob-sha")
        self.assertEqual(result.content, payload)
        first = opener.requests[0][0]
        second = opener.requests[1][0]
        self.assertEqual(first.get_method(), "GET")
        self.assertIn("/repos/owner/repo/contents/books/a%20b/state.bin", first.full_url)
        self.assertIn("ref=feature%2Ftest+branch", first.full_url)
        self.assertTrue(second.full_url.endswith("/repos/owner/repo/git/blobs/blob-sha"))
        headers = {key.lower(): value for key, value in first.header_items()}
        self.assertEqual(headers["accept"], "application/vnd.github+json")
        self.assertEqual(headers["x-github-api-version"], "2026-03-10")
        self.assertEqual(headers["authorization"], "Bearer secret-token")

    def test_tree_preserves_entries_and_truncated_flag(self):
        client, opener = self.client(
            encoded(
                {
                    "truncated": True,
                    "tree": [
                        {"path": "a.txt", "type": "blob", "sha": "a", "mode": "100644"},
                        {"path": "dir", "type": "tree", "sha": "b", "mode": "040000"},
                    ],
                }
            ),
            token=None,
        )

        result = client.get_tree("owner/repo", "feature/ref")

        self.assertTrue(result.truncated)
        self.assertEqual([item.path for item in result.entries], ["a.txt", "dir"])
        request = opener.requests[0][0]
        self.assertIn("/git/trees/feature%2Fref?recursive=1", request.full_url)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertNotIn("authorization", headers)

    def test_mutations_encode_content_branch_message_and_expected_sha(self):
        client, opener = self.client(
            encoded({"content": {"sha": "created"}, "commit": {"sha": "c1"}}),
            encoded({"content": {"sha": "updated"}, "commit": {"sha": "c2"}}),
            encoded({"content": None, "commit": {"sha": "c3"}}),
        )

        created = client.create_file("owner/repo", "a.txt", b"new", "branch", "create msg")
        updated = client.update_file("owner/repo", "a.txt", b"next", "created", "branch", "update msg")
        deleted = client.delete_file("owner/repo", "a.txt", "updated", "branch", "delete msg")

        self.assertEqual((created.blob_sha, created.commit_sha), ("created", "c1"))
        self.assertEqual((updated.blob_sha, updated.commit_sha), ("updated", "c2"))
        self.assertEqual((deleted.blob_sha, deleted.commit_sha), (None, "c3"))
        create_request, update_request, delete_request = [item[0] for item in opener.requests]
        self.assertEqual(create_request.get_method(), "PUT")
        self.assertEqual(update_request.get_method(), "PUT")
        self.assertEqual(delete_request.get_method(), "DELETE")
        create_body = json.loads(create_request.data.decode("utf-8"))
        update_body = json.loads(update_request.data.decode("utf-8"))
        delete_body = json.loads(delete_request.data.decode("utf-8"))
        self.assertEqual(create_body, {"branch": "branch", "content": "bmV3", "message": "create msg"})
        self.assertEqual(
            update_body,
            {"branch": "branch", "content": "bmV4dA==", "message": "update msg", "sha": "created"},
        )
        self.assertEqual(delete_body, {"branch": "branch", "message": "delete msg", "sha": "updated"})

    def test_http_transport_and_malformed_json_errors_are_sanitized(self):
        http_error = HTTPError(
            "https://api.github.com/example",
            403,
            "Forbidden secret-token",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"denied secret-token"}'),
        )
        client, _ = self.client(http_error)
        with self.assertRaises(GitHubApiError) as ctx:
            client.get_tree("owner/repo", "main")
        self.assertEqual(ctx.exception.status, 403)
        self.assertNotIn("secret-token", str(ctx.exception))

        client2, _ = self.client(b"not-json")
        with self.assertRaises(GitHubApiError):
            client2.get_tree("owner/repo", "main")

        client3, _ = self.client(URLError("offline"))
        with self.assertRaises(GitHubApiError) as ctx3:
            client3.get_tree("owner/repo", "main")
        self.assertIsNone(ctx3.exception.status)

    def test_invalid_file_shapes_and_base64_fail_closed(self):
        client, _ = self.client(encoded({"type": "dir", "sha": "x"}))
        with self.assertRaises(GitHubApiError):
            client.get_file("owner/repo", "path", "main")

        client2, _ = self.client(
            encoded({"type": "file", "sha": "blob"}),
            encoded({"encoding": "base64", "content": "%%%"}),
        )
        with self.assertRaises(GitHubApiError):
            client2.get_file("owner/repo", "path", "main")


if __name__ == "__main__":
    unittest.main()
