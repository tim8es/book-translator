import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
import blind_quality_export as export


class ExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.book = self.root / "books" / "fixture"
        self.book.mkdir(parents=True)
        self.rev = "a" * 40
        source, target = b"Seven coins.", "Семь монет.".encode()
        (self.book / "source.md").write_bytes(source)
        (self.book / "target.md").write_bytes(target)
        self.chapter = {"number": 1, "unit_id": "chapter-000001", "status": "reviewed",
                        "source_path": "source.md", "translation_path": "target.md",
                        "translation_acceptance": {"source_sha256": export.sha(source),
                                                   "translation_sha256": export.sha(target)}}
        self.write("metadata.json", {"workflow": {"resolved_revision": self.rev},
                    "source": {"sha256": "b" * 64}, "target_language": "ru"})
        self.write("progress.json", {"chapters": [self.chapter]})
        self.status = {"valid": True, "errors": [], "claims": [],
                       "workflow_revision": self.rev, "finalization": {"active": False},
                       "corpus": {"state": "verified"}, "units": [
                       {"unit_id": self.chapter["unit_id"], "chapter_number": 1,
                        "lifecycle": "reviewed", "review": "pass",
                        "source_path": "source.md", "translation_path": "target.md"}]}

    def write(self, path, value):
        (self.book / path).write_text(json.dumps(value), encoding="utf-8")

    def snapshot(self):
        with patch.object(export, "clean_head", return_value="c" * 40):
            return export.checked_snapshot(self.root, "fixture", lambda *_: self.status)

    def test_pair_validates_exact_source_and_workflow(self):
        a = self.snapshot()
        b = copy.deepcopy(a)
        self.assertEqual(len(export.paired(a, b)["units"]), 1)
        b["workflow_revision"] = "d" * 40
        with self.assertRaises(ValueError):
            export.paired(a, b)
        b = copy.deepcopy(a)
        b["units"][0]["source"]["text"] = "Changed"
        with self.assertRaises(ValueError):
            export.paired(a, b)

    def test_unreviewed_or_changed_artifact_rejected(self):
        self.status["units"][0]["review"] = "stale"
        with self.assertRaises(ValueError):
            self.snapshot()
        self.status["units"][0]["review"] = "pass"
        (self.book / "target.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_active_claim_and_outside_artifact_rejected(self):
        self.status["claims"] = [{}]
        with self.assertRaises(ValueError):
            self.snapshot()
        self.status["claims"] = []
        outside = self.root / "outside"
        outside.write_text("outside")
        (self.book / "target.md").unlink()
        (self.book / "target.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.snapshot()
