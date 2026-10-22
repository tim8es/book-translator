"""Wave worker routing emits paths, not chapter or literary content."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from workflow.context_routing import ContextRoutingError, contract_paths
from workflow.wave_cli import _read_role_routing, _wave_load_plan
from workflow.waves import WaveError


def fixtures():
    wave = {
        "wave_id": "b" * 32, "unit_ids": ["chapter-000001"],
        "memory_revision": "f" * 64, "source_sha256": "a" * 64,
        "workflow_revision": "revision-1",
        "shared_state_revisions": {"glossary": "g1", "style_guide": "s1"},
    }
    status = {
        "units": [{
            "unit_id": "chapter-000001", "chapter_number": 1,
            "source_path": "extracted/001-one.md",
            "translation_path": "translated/001-one.md",
        }]
    }
    decision = {
        "book_slug": "novel", "action": "claim", "unit_id": "chapter-000001",
        "chapter_number": 1, "operation": "translate", "role": "translator",
        "context": {
            "wave_id": wave["wave_id"], "memory_revision": wave["memory_revision"],
            "workflow_revision": wave["workflow_revision"],
            "source_sha256": wave["source_sha256"],
            "shared_state_revisions": wave["shared_state_revisions"],
        },
    }
    return wave, status, decision


class WaveRoleContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routing = _read_role_routing(ROOT)

    def test_translator_receives_short_wave_overlay_only(self):
        wave, status, decision = fixtures()
        plan = _wave_load_plan(decision, status, wave, self.routing)
        self.assertEqual(plan["role"], "translator")
        self.assertEqual(plan["instruction_paths"], [
            "AGENTS.md", "docs/TRANSLATION.md", "docs/WAVE_WORKER.md",
        ])
        self.assertNotIn("docs/ORCHESTRATION.md", plan["instruction_paths"])
        self.assertNotIn("docs/WAVES.md", plan["instruction_paths"])
        self.assertEqual(plan["phase"], "after_claim")
        self.assertEqual(plan["output_path"], "books/novel/translated/001-one.md")
        self.assertEqual([v["kind"] for v in plan["inputs"]],
                         ["original", "glossary", "style_guide"])
        self.assertEqual(plan["memory"]["revision"], wave["memory_revision"])
        self.assertFalse(plan["source_content_included"])
        self.assertEqual(plan, _wave_load_plan(decision, status, wave, self.routing))

    def test_resumed_claim_and_role_change_reselect_inputs(self):
        wave, status, decision = fixtures()
        translator = _wave_load_plan(decision, status, wave, self.routing)
        decision["action"] = "work"
        resumed = _wave_load_plan(decision, status, wave, self.routing)
        self.assertEqual(resumed["phase"], "live_claim")
        self.assertEqual(translator["instruction_paths"], resumed["instruction_paths"])
        decision["role"] = "reviewer"
        decision["operation"] = "review"
        reviewer = _wave_load_plan(decision, status, wave, self.routing)
        self.assertEqual(reviewer["role"], "reviewer")
        self.assertIsNone(reviewer["output_path"])
        self.assertEqual([v["kind"] for v in reviewer["inputs"]], [
            "original", "glossary", "style_guide", "translation", "review_ledger",
        ])
        self.assertEqual(reviewer["instruction_paths"], translator["instruction_paths"])
        # Role routing has no retained state from the previous role.
        decision["role"] = "translator"
        decision["operation"] = "correct_translation"
        correction = _wave_load_plan(decision, status, wave, self.routing)
        self.assertEqual([v["kind"] for v in correction["inputs"]],
                         [v["kind"] for v in reviewer["inputs"]])

    def test_no_work_returns_no_inputs(self):
        wave, status, decision = fixtures()
        for action in ("wait", "complete", "blocked"):
            decision["action"] = action
            self.assertIsNone(_wave_load_plan(decision, status, wave, self.routing))

    def test_drift_and_malformed_routing_fail_closed(self):
        wave, status, decision = fixtures()
        changed = copy.deepcopy(decision)
        changed["context"]["memory_revision"] = "e" * 64
        with self.assertRaisesRegex(WaveError, "pinned wave"):
            _wave_load_plan(changed, status, wave, self.routing)
        wave["unit_ids"] = []
        with self.assertRaisesRegex(WaveError, "not within"):
            _wave_load_plan(decision, status, wave, self.routing)
        wave, status, decision = fixtures()
        bad = copy.deepcopy(self.routing)
        bad["context_overlays"]["waves"]["translator"] = []
        with self.assertRaisesRegex(WaveError, "not configured"):
            _wave_load_plan(decision, status, wave, bad)

    def test_orchestrator_overlays_load_only_when_requested(self):
        base = contract_paths(self.routing, "orchestrator")
        self.assertEqual(base, ["AGENTS.md", "docs/ORCHESTRATION.md"])
        self.assertEqual(
            contract_paths(self.routing, "orchestrator", overlays=("source_update",)),
            base + ["docs/SOURCE_UPDATES.md"],
        )
        self.assertEqual(
            contract_paths(self.routing, "orchestrator", overlays=("waves",)),
            base + ["docs/WAVES.md"],
        )
        with self.assertRaises(ContextRoutingError):
            contract_paths(self.routing, "reviewer", overlays=("source_update",))

    def test_contract_overlays_are_conditional_not_base_profiles(self):
        manifest = self.routing
        self.assertNotIn("wave_worker", manifest["context_profiles"]["translator"])
        self.assertNotIn("wave_worker", manifest["context_profiles"]["reviewer"])
        self.assertEqual(manifest["context_overlays"]["waves"]["translator"],
                         ["wave_worker"])
        self.assertEqual(manifest["context_overlays"]["waves"]["reviewer"],
                         ["wave_worker"])
        self.assertEqual(manifest["context_overlays"]["waves"]["orchestrator"], ["waves"])
        self.assertTrue((ROOT / manifest["contracts"]["wave_worker"]).is_file())


if __name__ == "__main__":
    unittest.main()
