"""Wave state, memory, crash recovery and stale source regression tests."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.filesystem import FilesystemStorage
from workflow.literary_memory import MemoryError, snapshot_from, apply_decisions, context_pack
from workflow.waves import WaveError, WaveManager, _json_read


SHA = "a" * 64
NOW = datetime.now(timezone.utc)
WAVE = "b" * 32


def fake():
    return {
        "valid": True, "book_slug": "sample", "workflow_revision": "current",
        "state_revisions": {"glossary": "glossary-hash", "style_guide": "style-hash"},
        "finalization": {"active": False}, "claims": [],
        "units": [
            {
                "unit_id": f"chapter-{i:06d}", "chapter_number": i,
                "lifecycle": "extracted", "review": "untranslated",
                "source_path": f"extracted/{i}.md",
                "translation_path": f"translated/{i}.md",
            }
            for i in (1, 2)
        ],
        "corpus": {"state": "verified", "source_sha256": SHA},
    }


META = {"source": {"revision_id": "source-000001", "sha256": SHA}}


def entry():
    return {
        "memory_id": "term-warden", "kind": "term", "subject": "Warden",
        "statement": "In this scene Warden denotes a military title.",
        "translation_decision": "Страж",
        "evidence": {"unit_id": "chapter-000001", "quote": "Warden"},
        "source_revision_id": "source-000001",
        "status": "accepted", "supersedes": None,
    }


class WaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = FilesystemStorage(Path(self.temp.name))
        self.manager = WaveManager(self.storage)
        self.status = fake()

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        return self.manager.start(self.status, META, count=2, wave_id=WAVE)

    def accept(self):
        for unit in self.status["units"]:
            unit["lifecycle"] = "reviewed"
            unit["review"] = "pass"

    def test_one_active_wave_and_frozen_memory(self):
        plan = self.manager.plan(self.status, count=2)
        self.assertEqual(plan["unit_ids"], ["chapter-000001", "chapter-000002"])
        manifest = self.start()
        self.assertEqual(manifest["wave_id"], WAVE)
        self.assertEqual(self.manager.status(self.status)["unreviewed_units"],
                         ["chapter-000001", "chapter-000002"])
        with self.assertRaisesRegex(WaveError, "active wave"):
            self.manager.start(self.status, META, count=1)
        before = self.manager.memory()
        self.assertEqual(before["entries"], [])
        self.assertEqual(before["revision"], manifest["memory_revision"])
        self.assertEqual(
            context_pack(before, unit_id="chapter-000002", source=b"Warden.", previous_units=["chapter-000001"])["entries"],
            [],
        )

    def test_proposal_requires_claim_and_source_evidence(self):
        self.start()
        with self.assertRaises(WaveError):
            self.manager.submit(WAVE, "translator", entry(), source=b"Warden", source_revision="source-000001")
        claim = {
            "schema_version": 1, "role": "translator", "session_id": "translator",
            "unit_id": "chapter-000001",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }
        self.storage.create_if_absent(
            ".workflow/claims/chapter-000001.json",
            json.dumps(claim).encode(),
        )
        wrong = dict(entry(), evidence={"unit_id": "chapter-000001", "quote": "Not present"})
        with self.assertRaisesRegex(WaveError, "not present"):
            self.manager.submit(WAVE, "translator", wrong,
                                source=b"Warden", source_revision="source-000001")
        self.manager.submit(WAVE, "translator", entry(),
                            source=b"Warden", source_revision="source-000001")
        self.assertEqual(len(self.manager.proposals(WAVE)), 1)

    def test_reconciliation_blocks_unreviewed_units_then_closes_and_recovers(self):
        self.start()
        with self.assertRaisesRegex(WaveError, "independent PASS"):
            self.manager.reconcile_preview(
                self.status, decisions={},
                read_source=lambda _: b"Warden",
                read_translation=lambda _: "Страж".encode(),
            )
        self.accept()
        self.manager.prepare_close(
            self.status, decisions={},
            read_source=lambda _: b"Warden",
            read_translation=lambda _: "Страж".encode(),
        )
        self.assertEqual(self.manager.status(self.status)["state"]["phase"], "RECONCILING")
        result = self.manager.recover(wave_id=WAVE)
        self.assertEqual(result["state"], "CLOSED")
        self.assertIsNone(self.manager.active())
        self.assertEqual(self.manager.impact_queue(), [])

    def test_source_revision_mismatch_blocks_new_wave(self):
        manifest = self.start()
        self.accept()
        self.manager.prepare_close(
            self.status, decisions={},
            read_source=lambda _: b"Warden",
            read_translation=lambda _: "Страж".encode(),
        )
        self.manager.close(expected_wave=WAVE)
        self.status["units"][0]["lifecycle"] = "extracted"
        self.status["units"][0]["review"] = "untranslated"
        updated = {"source": {"revision_id": "source-000002", "sha256": "b" * 64}}
        with self.assertRaisesRegex(WaveError, "older source edition"):
            self.manager.start(self.status, updated, count=1)

    def test_reconciliation_detects_changed_shared_state(self):
        self.start()
        self.accept()
        self.status["state_revisions"]["glossary"] = "different"
        with self.assertRaisesRegex(WaveError, "changed during wave"):
            self.manager.reconcile_preview(
                self.status, decisions={},
                read_source=lambda _: b"Warden",
                read_translation=lambda _: b"trans",
            )

    def test_source_evidence_required_and_impact_candidates_identified(self):
        self.start()
        self.accept()
        claim = {
            "role": "translator", "session_id": "t", "unit_id": "chapter-000001",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }
        self.storage.create_if_absent(
            ".workflow/claims/chapter-000001.json", json.dumps(claim).encode()
        )
        self.manager.submit(WAVE, "t", entry(), source=b"Warden",
                            source_revision="source-000001")
        with self.assertRaisesRegex(WaveError, "active chapter claims"):
            self.manager.reconcile_preview(
                self.status | {"claims": [claim]},
                decisions={"term-warden": "accepted"},
                read_source=lambda _: b"Warden", read_translation=lambda _: b"Страж",
            )
        preview = self.manager.reconcile_preview(
            self.status, decisions={"term-warden": "accepted"},
            read_source=lambda _: b"Warden", read_translation=lambda _: "Страж".encode(),
        )
        self.assertEqual(len(preview["impact_candidates"]), 1)
        self.assertEqual(preview["impact_candidates"][0]["unit_id"], "chapter-000002")
        self.manager.prepare_close(
            self.status, decisions={"term-warden": "accepted"},
            read_source=lambda _: b"Warden", read_translation=lambda _: "Страж".encode(),
        )
        self.manager.close(expected_wave=WAVE)
        self.assertEqual(self.manager.memory()["entries"][0]["memory_id"], "term-warden")
        self.assertEqual(len(self.manager.impact_queue()), 1)

    def test_snapshot_no_silent_overwrite(self):
        snap = snapshot_from([], source_revision_id="source-000001",
                             glossary_revision="g", style_guide_revision="s")
        added = apply_decisions(snap, [entry()])
        self.assertEqual(len(added["entries"]), 1)
        with self.assertRaisesRegex(MemoryError, "different content"):
            apply_decisions(added, [dict(entry(), statement="Another claim")])

if __name__ == "__main__":
    unittest.main()
