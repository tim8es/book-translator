"""Bounded chat-session recovery based on immutable dispatches and durable status."""

import copy
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.filesystem import FilesystemStorage
from workflow.session_planner import plan_sessions
from workflow.session_runtime import (
    SessionRuntimeError, load_dispatch, next_action, save_dispatch,
)


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
DISPATCH = "a" * 32


def status():
    return {
        "valid": True, "book_slug": "sample", "workflow_revision": "rev",
        "state_revisions": {"glossary": "g1", "style_guide": "s1"},
        "finalization": {"active": False}, "claims": [],
        "corpus": {"state": "verified", "source_sha256": "a" * 64},
        "units": [
            {"unit_id": f"chapter-{i:06d}", "chapter_number": i,
             "lifecycle": "extracted", "review": "untranslated"}
            for i in range(1, 4)
        ],
    }


def plan(book=None):
    book = book or status()
    return plan_sessions(
        book, sessions=2,
        source_sizes={u["unit_id"]: 100 for u in book["units"]},
        base_commit="git-head", dispatch_id=DISPATCH,
    )


class SessionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = FilesystemStorage(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_save_load_is_idempotent_and_never_replaces_another_dispatch(self):
        sample = plan()
        path = save_dispatch(self.storage, sample)
        self.assertEqual(path, f".workflow/session-dispatches/{DISPATCH}.json")
        self.assertEqual(load_dispatch(self.storage, DISPATCH), sample)
        self.assertEqual(save_dispatch(self.storage, sample), path)
        changed = copy.deepcopy(sample)
        changed["sessions"][0]["units"][0]["source_bytes"] = 999
        with self.assertRaisesRegex(SessionRuntimeError, "different plan"):
            save_dispatch(self.storage, changed)

    def test_resumes_from_first_unfinished_after_restart(self):
        original = status()
        saved = plan(original)
        save_dispatch(self.storage, saved)
        session_id = saved["sessions"][0]["session_id"]
        decision = next_action(load_dispatch(self.storage, DISPATCH), original,
                               session_id, now=NOW)
        self.assertEqual(decision["action"], "claim")
        unit_id = decision["unit_id"]
        original["claims"] = [{
            "unit_id": unit_id, "role": "translator", "session_id": session_id,
            "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
        }]
        running = next_action(saved, original, session_id, now=NOW)
        self.assertEqual(running["action"], "work")
        self.assertEqual(running["unit_id"], unit_id)

        original["claims"].clear()
        first = next(u for u in original["units"] if u["unit_id"] == unit_id)
        first["lifecycle"] = "translated"
        first["review"] = "missing"
        after_acceptance = next_action(saved, original, session_id, now=NOW)
        self.assertNotEqual(after_acceptance.get("unit_id"), unit_id)
        self.assertIn(after_acceptance["action"], {"claim", "wait"})
        first["lifecycle"] = "reviewed"
        first["review"] = "pass"
        progressed = next_action(saved, original, session_id, now=NOW)
        self.assertNotEqual(progressed.get("unit_id"), unit_id)

    def test_rejected_expired_claim_is_not_reused(self):
        snapshot = status()
        saved = plan(snapshot)
        session_id = saved["sessions"][0]["session_id"]
        unit_id = saved["sessions"][0]["units"][0]["unit_id"]
        snapshot["claims"] = [{
            "unit_id": unit_id, "role": "translator", "session_id": session_id,
            "expires_at": NOW.isoformat(),
        }]
        decision = next_action(saved, snapshot, session_id, now=NOW)
        self.assertEqual((decision["action"], decision["reason"]),
                         ("blocked", "claim_expired"))

    def test_source_revision_change_blocks_stale_chapter_claim(self):
        snapshot = status()
        saved = plan(snapshot)
        snapshot["corpus"]["source_sha256"] = "b" * 64
        decision = next_action(saved, snapshot, saved["sessions"][0]["session_id"], now=NOW)
        self.assertEqual(decision["reason"], "source_revision_changed")

    def test_revision_drift_and_preflight_block_work(self):
        snapshot = status()
        saved = plan(snapshot)
        session_id = saved["sessions"][0]["session_id"]
        snapshot["state_revisions"]["glossary"] = "new-g"
        self.assertEqual(next_action(saved, snapshot, session_id, now=NOW)["reason"],
                         "shared_state_changed")
        snapshot["valid"] = False
        self.assertEqual(next_action(saved, snapshot, session_id, now=NOW)["reason"],
                         "preflight_failed")

    def test_session_identity_and_missing_unit_fail_closed(self):
        saved = plan()
        snapshot = status()
        with self.assertRaises(SessionRuntimeError):
            next_action(saved, snapshot, "other-session", now=NOW)
        snapshot["units"].pop(0)
        decision = next_action(saved, snapshot, saved["sessions"][0]["session_id"], now=NOW)
        self.assertEqual(decision["reason"], "unit_missing_or_source_revised")

    def test_complete_requires_durable_reviewed_state(self):
        snapshot = status()
        saved = plan(snapshot)
        selected = saved["sessions"][0]
        for unit in snapshot["units"]:
            if unit["unit_id"] in {x["unit_id"] for x in selected["units"]}:
                unit["lifecycle"], unit["review"] = "reviewed", "pass"
        decision = next_action(saved, snapshot, selected["session_id"], now=NOW)
        self.assertEqual(decision["action"], "complete")
        self.assertEqual(decision["completed_units"], len(selected["units"]))


if __name__ == "__main__":
    unittest.main()
