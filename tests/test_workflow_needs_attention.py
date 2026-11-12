"""Canonical exact-review needs-attention scheduling tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.attention import (
    ATTENTION_PATH, AttentionError, AttentionManager, active_attention,
)
from workflow.filesystem import FilesystemStorage
from workflow.repository import WorkflowStateRepository
from workflow.status import StatusResolver


class NeedsAttentionTests(unittest.TestCase):
    REVISION = "a" * 40

    def resolution(self, *, outcome="CORRECTIONS_REQUIRED", sha="b" * 64):
        return SimpleNamespace(unit_id="chapter-000001", source_sha256="c" * 64,
                               translation_sha256=sha,
                               state=("corrections_required" if outcome == "CORRECTIONS_REQUIRED"
                                      else "pass"),
                               current_record={"record_id": "d" * 32, "outcome": outcome})

    def test_hash_bound_cas_and_invalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = FilesystemStorage(Path(temp))
            manager = AttentionManager(WorkflowStateRepository(storage))
            resolution = self.resolution()
            first = manager.defer(self.REVISION, resolution, reason="correction-limit")
            second = manager.defer(self.REVISION, resolution, reason="correction-limit")
            self.assertEqual(first, second)
            self.assertEqual(len(manager.load(self.REVISION)[0]["entries"]), 1)
            chapters = [{"unit_id": "chapter-000001", "number": 1, "status": "translated"}]
            entries = manager.load(self.REVISION)[0]["entries"]
            self.assertEqual(active_attention(entries, [resolution], chapters),
                             ["chapter-000001"])
            self.assertEqual(active_attention(
                entries, [self.resolution(sha="e" * 64)], chapters), [])
            self.assertEqual(active_attention(
                entries, [self.resolution(outcome="PASS")], chapters), [])
            with self.assertRaises(AttentionError):
                manager.defer(self.REVISION, self.resolution(outcome="PASS"),
                              reason="correction-limit")
            with self.assertRaises(AttentionError):
                manager.load("f" * 40)

    def test_malformed_queue_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / ".workflow").mkdir()
            (folder / ATTENTION_PATH).write_text('{"schema_version":1,"entries":[]}')
            manager = AttentionManager(WorkflowStateRepository(FilesystemStorage(folder)))
            with self.assertRaises(AttentionError):
                manager.load(self.REVISION)

    def test_resume_skips_deferred_but_never_reports_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            resolver = StatusResolver(
                WorkflowStateRepository(FilesystemStorage(Path(temp))),
                artifact_reader=lambda _: b"")
            units = [
                {"unit_id": "chapter-000001", "chapter_number": 1,
                 "lifecycle": "translated", "review": "corrections_required"},
                {"unit_id": "chapter-000002", "chapter_number": 2,
                 "lifecycle": "extracted", "review": "untranslated"},
            ]
            snapshot = {"schema_version": 1, "valid": True, "claims": [],
                        "finalization": {"active": False}, "units": units,
                        "needs_attention": ["chapter-000001"]}
            with patch.object(resolver, "_context", return_value={"role": "orchestrator"}):
                decision = resolver.resume(snapshot)
                self.assertEqual((decision["operation"], decision["unit_id"]),
                                 ("translate", "chapter-000002"))
                units.pop()
                decision = resolver.resume(snapshot)
                self.assertEqual(decision["operation"], "needs_attention")
                self.assertEqual(decision["units"], ["chapter-000001"])
                with self.assertRaisesRegex(Exception, "invalid needs-attention"):
                    resolver.resume({**snapshot, "needs_attention": ["chapter-999999"]})


if __name__ == "__main__":
    unittest.main()
