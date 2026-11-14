"""ChatOps queue grouping is a public, deterministic and book-safe operation."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chat_ops_discover", ROOT / "scripts" / "chat_ops_discover.py")
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class ChatOpsDiscoveryTests(unittest.TestCase):
    def test_groups_pending_by_book_and_retains_invalid_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests = root / ".chat-ops/requests"
            receipts = root / ".chat-ops/results"
            requests.mkdir(parents=True)
            receipts.mkdir()
            for idx, content in enumerate([
                '{"book_slug":"synthetic-a"}',
                '{"book_slug":"synthetic-a"}',
                '{"book_slug":"synthetic-b"}',
                '{"book_slug":"../bad"}',
                '{"book_slug":"synthetic-c","book_slug":"synthetic-d"}',
            ], start=1):
                (requests / f"{idx:032x}.json").write_text(
                    content, encoding="utf-8")
            (receipts / f"{1:032x}.json").write_text("{}", encoding="utf-8")
            (requests / "bad-name.json").write_text('{"book_slug":"ignored"}')
            self.assertEqual(discovery.pending_books(root),
                             ["__invalid__", "synthetic-a", "synthetic-b"])
            outputs = root / "outputs"
            self.assertEqual(discovery.main([
                "--root", str(root), "--github-output", str(outputs)]), 0)
            lines = dict(line.split("=", 1) for line in
                         outputs.read_text("utf-8").splitlines())
            self.assertTrue(json.loads(lines["has_pending"]))
            self.assertEqual(json.loads(lines["books"]),
                             ["__invalid__", "synthetic-a", "synthetic-b"])

    def test_empty_queue_has_no_pending_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "outputs"
            self.assertEqual(discovery.pending_books(Path(tmp)), [])
            discovery.main(["--root", tmp, "--github-output", str(p)])
            self.assertEqual(p.read_text("utf-8"),
                             'books=["__none__"]\nhas_pending=false\n')

    def test_bounded_discovery(self):
        with self.assertRaises(ValueError):
            discovery.pending_books(Path("/"), 201)
        with self.assertRaises(ValueError):
            discovery.pending_books(Path("/"), 0)


if __name__ == "__main__":
    unittest.main()
