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


if __name__ == "__main__":
    unittest.main()
