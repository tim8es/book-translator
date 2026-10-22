"""Wave state, memory, crash recovery and stale source regression tests."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
        "state_revisions": {
            "glossary": "glossary-hash", "style_guide": "style-hash",
            "metadata": "metadata-revision", "progress": "progress-revision",
            "review_ledger": "ledger-revision",
        },
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

    def source(self, _):
        return b"Warden"

    def translation(self, _):
        return "Страж".encode()

    def prepare(self, decisions=None):
        return self.manager.prepare_close(
            self.status, decisions=decisions or {},
            read_source=self.source, read_translation=self.translation,
            fresh_status=lambda: self.status,
        )

    def close(self):
        return self.manager.close(
            expected_wave=WAVE, fresh_status=lambda: self.status,
            read_source=self.source, read_translation=self.translation,
        )

    def recover(self):
        return self.manager.recover(
            wave_id=WAVE, fresh_status=lambda: self.status,
            read_source=self.source, read_translation=self.translation,
        )

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
        self.prepare()
        self.assertEqual(self.manager.status(self.status)["state"]["phase"], "RECONCILING")
        result = self.recover()
        self.assertEqual(result["state"], "CLOSED")
        self.assertIsNone(self.manager.active())
        self.assertEqual(self.manager.impact_queue(), [])

    def test_source_revision_mismatch_blocks_new_wave(self):
        manifest = self.start()
        self.accept()
        self.prepare()
        self.close()
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
                read_source=lambda _: b"Warden", read_translation=lambda _: b"rank",
            )
        claim_path = ".workflow/claims/chapter-000001.json"
        current = self.storage.read(claim_path)
        self.storage.delete_if_version(claim_path, current.version)
        preview = self.manager.reconcile_preview(
            self.status, decisions={"term-warden": "accepted"},
            read_source=lambda _: b"Warden", read_translation=lambda _: "Страж".encode(),
        )
        self.assertEqual(len(preview["impact_candidates"]), 1)
        self.assertEqual(preview["impact_candidates"][0]["unit_id"], "chapter-000002")
        self.prepare({"term-warden": "accepted"})
        self.close()
        self.assertEqual(self.manager.memory()["entries"][0]["memory_id"], "term-warden")
        self.assertEqual(len(self.manager.impact_queue()), 1)

    def test_between_wave_shared_state_sync_creates_impact_review_queue(self):
        self.start()
        self.accept()
        self.prepare()
        self.close()
        self.status["state_revisions"]["glossary"] = "new-glossary-hash"
        updated = self.manager.sync_shared_memory(self.status, META)
        self.assertEqual(updated["state"], "synchronized")
        self.assertEqual(updated["impact_count"], 2)
        self.assertEqual(len(self.manager.impact_queue()), 2)
        self.assertEqual(self.manager.memory()["glossary_revision"], "new-glossary-hash")
        self.assertEqual(
            self.manager.sync_shared_memory(self.status, META)["state"], "unchanged"
        )
        self.status["units"][0]["lifecycle"] = "extracted"
        self.status["units"][0]["review"] = "untranslated"
        next_wave = self.manager.start(self.status, META, count=1, wave_id="c" * 32)
        self.assertEqual(next_wave["memory_revision"], updated["memory_revision"])

    def test_close_rejects_changed_glossary_metadata_review_and_chapter_bytes(self):
        for field, mutated in (
            ("glossary", "new-glossary"),
            ("metadata", "new-metadata"),
            ("progress", "new-progress"),
            ("review_ledger", "new-ledger"),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as folder:
                    self.storage = FilesystemStorage(Path(folder))
                    self.manager = WaveManager(self.storage)
                    self.status = fake()
                    self.start()
                    self.accept()
                    self.prepare()
                    before_memory = self.manager.memory()["revision"]
                    self.status["state_revisions"][field] = mutated
                    with self.assertRaises(WaveError):
                        self.close()
                    self.assertEqual(self.manager.memory()["revision"], before_memory)
                    self.assertEqual(self.manager.status(self.status)["state"]["phase"], "RECONCILING")
                    self.status["state_revisions"][field] = fake()["state_revisions"][field]
                    self.assertEqual(self.recover()["state"], "CLOSED")

        with tempfile.TemporaryDirectory() as folder:
            self.storage = FilesystemStorage(Path(folder))
            self.manager = WaveManager(self.storage)
            self.status = fake()
            self.start()
            self.accept()
            self.prepare()
            with patch.object(self, "translation", return_value=b"new translation"):
                with self.assertRaisesRegex(WaveError, "changed since reconciliation"):
                    self.close()
            self.assertEqual(self.recover()["state"], "CLOSED")

    def test_close_rejects_changed_source_and_revoked_pass(self):
        self.start()
        self.accept()
        self.prepare()
        original = self.manager.memory()["revision"]
        self.status["corpus"]["source_sha256"] = "c" * 64
        with self.assertRaisesRegex(WaveError, "source fingerprint"):
            self.close()
        self.status["corpus"]["source_sha256"] = SHA
        self.status["units"][0]["review"] = "stale"
        self.status["valid"] = False
        with self.assertRaises(WaveError):
            self.close()
        self.assertEqual(self.manager.memory()["revision"], original)
        self.status["units"][0]["review"] = "pass"
        self.status["valid"] = True
        self.assertEqual(self.recover()["state"], "CLOSED")

    def test_resolved_impact_reopens_when_translation_or_pass_changes(self):
        self.start()
        self.accept()
        self.prepare()
        self.close()
        revision = self.manager.memory()["revision"]
        sync_id = "c" * 32
        self.storage.create_if_absent(
            f".workflow/literary-memory/impacts/{sync_id}.json",
            json.dumps({
                "schema_version": 1, "wave_id": sync_id,
                "memory_revision": revision,
                "candidates": [{
                    "memory_id": "character-voice", "unit_id": "chapter-000001",
                    "reason": "candidate_text_match", "matched_terms": ["Warden"],
                    "state": "pending",
                }],
            }).encode(),
        )
        self.assertEqual(len(self.manager.impact_queue()), 1)
        resolution_path = (
            f".workflow/literary-memory/resolutions/{sync_id}/"
            "character-voice-chapter-000001.json"
        )
        self.storage.create_if_absent(resolution_path, json.dumps({
            "schema_version": 1, "wave_id": sync_id,
            "memory_id": "character-voice", "unit_id": "chapter-000001",
            "reviewed": True, "memory_revision": revision,
            "review_record_id": "pass-a", "translation_sha256": "1" * 64,
        }).encode())
        with patch.object(self.manager, "_current_impact_reviews", return_value={
            "chapter-000001": {
                "record_id": "pass-a", "translation_sha256": "1" * 64,
                "source_sha256": SHA,
            }
        }):
            self.assertEqual(self.manager.impact_queue(), [])
        for new_record, new_sha in (("pass-a", "2" * 64), ("pass-b", "1" * 64)):
            with self.subTest(record=new_record, sha=new_sha):
                with patch.object(self.manager, "_current_impact_reviews", return_value={
                    "chapter-000001": {
                        "record_id": new_record, "translation_sha256": new_sha,
                        "source_sha256": SHA,
                    }
                }):
                    self.assertEqual(len(self.manager.impact_queue()), 1)
                    self.assertEqual(self.manager.impact_queue()[0]["reason"],
                                     "previous_resolution_stale")

    def test_snapshot_no_silent_overwrite(self):
        snap = snapshot_from([], source_revision_id="source-000001",
                             glossary_revision="g", style_guide_revision="s")
        added = apply_decisions(snap, [entry()])
        self.assertEqual(len(added["entries"]), 1)
        with self.assertRaisesRegex(MemoryError, "different content"):
            apply_decisions(added, [dict(entry(), statement="Another claim")])

if __name__ == "__main__":
    unittest.main()
