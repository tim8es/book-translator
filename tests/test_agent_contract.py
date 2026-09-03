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
        self.assertFalse(policy["silent_mid_run_upgrade"])

    def test_contract_read_order_is_single_and_complete(self):
        manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
        expected = [
            "agent-manifest.json",
            "SKILL.md",
            "AGENTS.md",
            "docs/AGENT_SETUP.md",
            "docs/ORCHESTRATION.md",
        ]

        self.assertEqual(manifest["contract_read_order"], expected)

        for relative_path in (
            "README.md",
            "SKILL.md",
            "AGENTS.md",
            "docs/AGENT_SETUP.md",
            "docs/TRANSLATION_GUIDE.md",
        ):
            text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("contract_read_order", text, msg=relative_path)

    def test_authoritative_bootstrap_checks_out_resolved_revision(self):
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("git checkout --detach <resolved-revision>", agents)
        self.assertNotIn("git clone --depth 1", agents)

    def test_existing_repository_install_has_deterministic_collision_policy(self):
        setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")

        self.assertIn("Existing repository collision policy", setup)
        self.assertIn("Never overwrite a pre-existing unrelated file", setup)
        self.assertIn(".book-translator/", setup)
        self.assertIn("install_root", setup)

    def test_book_metadata_template_records_workflow_provenance(self):
        metadata = json.loads((PROJECT_ROOT / "docs" / "templates" / "metadata.json").read_text(encoding="utf-8"))
        workflow = metadata["workflow"]

        self.assertEqual(workflow["repository"], "https://github.com/tim8es/book-translator")
        self.assertIn("requested_ref", workflow)
        self.assertIn("resolved_revision", workflow)

        guide = (PROJECT_ROOT / "docs" / "TRANSLATION_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("resolved_revision", guide)
        self.assertIn("docs/ORCHESTRATION.md", guide)

    def test_manifest_prefers_isolated_workers_with_portable_fallback(self):
        manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
        execution = manifest["execution"]

        self.assertEqual(execution["preferred_mode"], "isolated_workers")
        self.assertEqual(execution["fallback_mode"], "single_agent_bounded_context")
        self.assertEqual(execution["chapter_policy"], "sequential")
        self.assertTrue(execution["fresh_translator_per_chapter"])
        self.assertTrue(execution["fresh_reviewer_per_chapter"])
        self.assertTrue(execution["reviewer_independent_from_translator"])
        self.assertEqual(execution["global_state_writer"], "orchestrator")
        self.assertFalse(execution["parallel_chapter_translation_by_default"])

    def test_orchestration_protocol_is_explicit_and_single_writer(self):
        orchestration = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")

        self.assertIn("Translator worker", orchestration)
        self.assertIn("Reviewer worker", orchestration)
        self.assertIn("single writer", orchestration.lower())
        self.assertIn("Only the orchestrator may update global mutable state", orchestration)
        self.assertIn("single_agent_bounded_context", setup)
        self.assertIn("Do not translate multiple chapters concurrently by default", setup)

    def test_skill_preserves_one_link_bootstrap(self):
        skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("https://github.com/tim8es/book-translator", skill)
        self.assertIn("source book", skill.lower())
        self.assertIn("target language", skill.lower())
        self.assertIn("Do not require the user to describe the orchestration", skill)


if __name__ == "__main__":
    unittest.main()
