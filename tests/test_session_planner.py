"""Deterministic read-only session workpack tests."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow.session_planner import SessionPlanError, plan_sessions

DISPATCH = "a" * 32


def book_status():
    units = [
        {"unit_id": f"chapter-{n:06d}", "chapter_number": n, "lifecycle": "extracted",
         "review": "untranslated", "source_path": f"extracted/{n}.md",
         "translation_path": f"translated/{n}.md"}
        for n in range(1, 7)
    ]
    return {
        "valid": True, "book_slug": "novel", "workflow_revision": "revision",
        "finalization": {"active": False}, "claims": [],
        "units": units, "state_revisions": {"glossary": "g1", "style_guide": "s1"},
    }


class SessionPlannerTests(unittest.TestCase):
    def test_balanced_workpacks_are_stable_disjoint_and_cover_pending_units(self):
        state = book_status()
        sizes = {u["unit_id"]: weight for u, weight in
                 zip(state["units"], [500, 200, 350, 450, 200, 100])}
        first = plan_sessions(state, sessions=3, source_sizes=sizes, base_commit="abc123", dispatch_id=DISPATCH)
        self.assertEqual(first, plan_sessions(state, sessions=3, source_sizes=sizes, base_commit="abc123", dispatch_id=DISPATCH))
        self.assertEqual(len(first["sessions"]), 3)
        numbers = [n for batch in first["sessions"] for n in batch["chapter_numbers"]]
        self.assertEqual(numbers, list(range(1, 7)))
        self.assertEqual(len(set(s["session_id"] for s in first["sessions"])), 3)
        self.assertEqual(sum(s["source_bytes"] for s in first["sessions"]), 1800)

    def test_new_dispatch_id_never_reuses_owner_session_identity(self):
        state = book_status()
        sizes = {u["unit_id"]: 10 for u in state["units"]}
        first = plan_sessions(state, sessions=2, source_sizes=sizes,
                              base_commit="same-commit", dispatch_id=DISPATCH)
        second = plan_sessions(state, sessions=2, source_sizes=sizes,
                               base_commit="same-commit", dispatch_id="b" * 32)
        self.assertEqual(
            [s["chapter_numbers"] for s in first["sessions"]],
            [s["chapter_numbers"] for s in second["sessions"]],
        )
        self.assertTrue(
            set(s["session_id"] for s in first["sessions"]).isdisjoint(
                s["session_id"] for s in second["sessions"]
            )
        )

    def test_claimed_and_reviewed_units_are_not_dispatched(self):
        state = book_status()
        state["units"][0]["lifecycle"] = "reviewed"
        state["claims"] = [{"unit_id": "chapter-000003"}]
        sizes = {u["unit_id"]: 100 for u in state["units"]}
        result = plan_sessions(state, sessions=8, source_sizes=sizes, base_commit="abc", dispatch_id=DISPATCH)
        self.assertEqual([n for s in result["sessions"] for n in s["chapter_numbers"]], [2, 4, 5, 6])
        self.assertEqual(len(result["sessions"]), 4)

    def test_invalid_preflight_is_fail_closed(self):
        state = book_status()
        state["valid"] = False
        with self.assertRaises(SessionPlanError):
            plan_sessions(state, sessions=2, source_sizes={}, base_commit="abc", dispatch_id=DISPATCH)

    def test_missing_source_length_rejected(self):
        with self.assertRaisesRegex(SessionPlanError, "byte count"):
            plan_sessions(book_status(), sessions=2, source_sizes={}, base_commit="abc", dispatch_id=DISPATCH)


if __name__ == "__main__":
    unittest.main()
