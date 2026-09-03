import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AgentContractTests(unittest.TestCase):
    def test_public_contract_declares_primary_and_agent_agnostic_usage(self):
        manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["primary_clients"], ["ChatGPT Web", "Codex"])
        self.assertTrue(manifest["agent_agnostic"])
        self.assertIn("ChatGPT Web", readme)
        self.assertIn("Codex", readme)
        self.assertIn("other capable AI agents", readme)
        self.assertIn("agent-agnostic", skill.lower())

    def test_version_policy_requires_resolved_revision_record(self):
        manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
        policy = manifest["version_policy"]

        self.assertTrue(policy["record_resolved_revision"])
        self.assertEqual(policy["resolved_revision_file"], ".book-translator-install.json")

    def test_manifest_prefers_isolated_workers_with_portable_fallback(self):
        manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
        execution = manifest["execution"]

        self.assertEqual(execution["preferred_mode"], "isolated_workers")
        self.assertEqual(execution["fallback_mode"], "single_agent_bounded_context")
        self.assertEqual(execution["chapter_policy"], "sequential")
        self.assertTrue(execution["fresh_translator_per_chapter"])
        self.assertTrue(execution["fresh_reviewer_per_chapter"])
        self.assertEqual(execution["global_state_writer"], "orchestrator")

    def test_orchestration_protocol_is_explicit_and_single_writer(self):
        orchestration = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("Translator worker", orchestration)
        self.assertIn("Reviewer worker", orchestration)
        self.assertIn("single writer", orchestration.lower())
        self.assertIn("Only the orchestrator may update global mutable state", orchestration)
        self.assertIn("isolated workers", agents.lower())
        self.assertIn("single-agent bounded-context fallback", agents.lower())

    def test_skill_preserves_one_link_bootstrap(self):
        skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("https://github.com/tim8es/book-translator", skill)
        self.assertIn("source book", skill.lower())
        self.assertIn("target language", skill.lower())
        self.assertIn("Do not require the user to describe the orchestration", skill)


if __name__ == "__main__":
    unittest.main()
