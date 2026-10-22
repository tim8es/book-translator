"""Mutating CLI commands must not run an installation from another revision."""

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book
import corpus


class PinnedRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "books" / "novel").mkdir(parents=True)
        self.write(self.root / ".book-translator-install.json", {
            "resolved_revision": "a" * 40,
        })
        self.write(self.root / "books" / "novel" / "metadata.json", {
            "workflow": {"resolved_revision": "a" * 40},
        })

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def write(path, obj):
        path.write_text(json.dumps(obj) + "\n", encoding="utf-8")

    def parser(self, command, func, *, save=False):
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command", required=True)
        cmd = sub.add_parser(command)
        cmd.add_argument("slug")
        cmd.set_defaults(func=func, save=save)
        return parser

    def test_matching_pin_allows_mutating_operation(self):
        with patch.object(book, "repo_root", return_value=self.root):
            book.require_compatible_workflow_revision("novel")

    def test_mismatch_fails_closed_and_readonly_remains_usable(self):
        self.write(self.root / "books" / "novel" / "metadata.json", {
            "workflow": {"resolved_revision": "b" * 40},
        })
        called = []
        with (
            patch.object(book, "repo_root", return_value=self.root),
            patch.object(book, "build_parser", side_effect=lambda: self.parser("claim", lambda _: called.append("mutate") or 0)),
        ):
            self.assertEqual(book.main(["claim", "novel"]), 1)
        self.assertEqual(called, [])
        with (
            patch.object(book, "repo_root", return_value=self.root),
            patch.object(book, "build_parser", side_effect=lambda: self.parser("status", lambda _: called.append("read") or 0)),
        ):
            self.assertEqual(book.main(["status", "novel"]), 0)
        self.assertEqual(called, ["read"])

    def test_corpus_restoration_blocks_mismatch_but_verify_still_works(self):
        self.write(self.root / "books" / "novel" / "metadata.json", {
            "workflow": {"resolved_revision": "b" * 40},
        })
        invoked = []
        with (
            patch.object(book, "repo_root", return_value=self.root),
            patch.object(corpus, "build_parser", side_effect=lambda: self.parser("restore", lambda _: invoked.append("restore") or 0)),
        ):
            self.assertEqual(corpus.main(["restore", "novel"]), 1)
        with (
            patch.object(book, "repo_root", return_value=self.root),
            patch.object(corpus, "build_parser", side_effect=lambda: self.parser("verify", lambda _: invoked.append("verify") or 0)),
        ):
            self.assertEqual(corpus.main(["verify", "novel"]), 0)
        self.assertEqual(invoked, ["verify"])

    def test_missing_concrete_pin_refuses_mutation(self):
        self.write(self.root / ".book-translator-install.json", {"resolved_revision": None})
        with patch.object(book, "repo_root", return_value=self.root):
            with self.assertRaisesRegex(book.BookError, "concrete"):
                book.require_compatible_workflow_revision("novel")


if __name__ == "__main__":
    unittest.main()
