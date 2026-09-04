import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AgentContractTests(unittest.TestCase):
    def load_manifest(self):
        return json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))

    def test_public_contract_declares_primary_and_agent_agnostic_usage(self):
        manifest = self.load_manifest()
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["primary_clients"], ["ChatGPT Web", "Codex"])
        self.assertTrue(manifest["agent_agnostic"])
        self.assertIn("ChatGPT Web", readme)
        self.assertIn("Codex", readme)
        self.assertIn("other capable AI agents", readme)
        self.assertIn("agent-agnostic", skill.lower())

    def test_version_policy_requires_resolved_revision_record(self):
        policy = self.load_manifest()["version_policy"]

        self.assertTrue(policy["record_resolved_revision"])
        self.assertEqual(policy["resolved_revision_file"], ".book-translator-install.json")
        self.assertEqual(policy["per_book_provenance"], "metadata.json.workflow")
        self.assertFalse(policy["silent_mid_run_upgrade"])

    def test_manifest_routes_roles_to_minimal_contracts(self):
        manifest = self.load_manifest()

        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(
            manifest["contracts"],
            {
                "global": "AGENTS.md",
                "setup": "docs/AGENT_SETUP.md",
                "orchestration": "docs/ORCHESTRATION.md",
                "translation": "docs/TRANSLATION.md",
            },
        )
        self.assertEqual(manifest["context_profiles"]["bootstrap"], ["global", "setup"])
        self.assertEqual(manifest["context_profiles"]["orchestrator"], ["global", "orchestration"])
        self.assertEqual(manifest["context_profiles"]["translator"], ["global", "translation"])
        self.assertEqual(manifest["context_profiles"]["reviewer"], ["global", "translation"])
        self.assertNotIn("contract_read_order", manifest)

    def test_context_profiles_reference_only_declared_contracts(self):
        manifest = self.load_manifest()
        declared = set(manifest["contracts"])

        for profile, keys in manifest["context_profiles"].items():
            self.assertTrue(keys, msg=profile)
            self.assertTrue(set(keys) <= declared, msg=profile)
            for key in keys:
                self.assertTrue((PROJECT_ROOT / manifest["contracts"][key]).is_file(), msg=f"{profile}:{key}")

    def test_role_profiles_exclude_irrelevant_contracts(self):
        profiles = self.load_manifest()["context_profiles"]

        self.assertNotIn("translation", profiles["bootstrap"])
        self.assertNotIn("orchestration", profiles["bootstrap"])
        self.assertNotIn("setup", profiles["orchestrator"])
        self.assertNotIn("translation", profiles["orchestrator"])
        self.assertNotIn("setup", profiles["translator"])
        self.assertNotIn("orchestration", profiles["translator"])
        self.assertNotIn("setup", profiles["reviewer"])
        self.assertNotIn("orchestration", profiles["reviewer"])

    def test_manifest_does_not_duplicate_human_execution_rules(self):
        manifest = self.load_manifest()

        self.assertNotIn("execution", manifest)
        self.assertNotIn("completion_requirement", manifest)
        self.assertNotIn("agent_behavior", manifest)
        self.assertNotIn("capability_fallbacks", manifest)

    def test_legacy_translation_guide_is_removed(self):
        self.assertFalse((PROJECT_ROOT / "docs" / "TRANSLATION_GUIDE.md").exists())
        self.assertTrue((PROJECT_ROOT / "docs" / "TRANSLATION.md").is_file())

    def test_public_tree_excludes_internal_development_specs(self):
        self.assertFalse((PROJECT_ROOT / "docs" / "superpowers").exists())
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("docs/superpowers", readme)
        self.assertNotIn("implementation plan", readme)

    def test_readme_is_explicitly_non_normative(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()

        self.assertIn("not part of the agent execution contract", readme)
        self.assertIn("docs/translation.md", readme)

    def test_readme_guides_zero_user_through_usage_modes(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").lower()

        for phrase in (
            "start in 30 seconds",
            "choose how to use it",
            "option 1 — use it directly in a web ai",
            "option 2 — use a private github workspace",
            "recommended for full-book and multi-session translations",
            "option 3 — use it on your computer",
            "one workspace can contain many books",
            "how resuming works",
            "what book translator does for you",
            "frequently asked questions",
            "how it works internally",
            "no api key",
            "no programming knowledge",
        ):
            self.assertIn(phrase, readme)

        self.assertNotIn("10 chapters", readme)
        self.assertNotIn("ten chapters", readme)

    def test_skill_is_discovery_only(self):
        skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("context_profiles", skill)
        self.assertNotIn("Translator worker", skill)
        self.assertNotIn("Reviewer worker", skill)
        self.assertNotIn("single writer", skill.lower())
        self.assertNotIn("progress.json", skill)

    def test_global_contract_stays_role_agnostic(self):
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("context_profiles", agents)
        self.assertNotIn("git clone https://github.com/tim8es/book-translator.git", agents)
        self.assertNotIn("Stage 2 — fidelity review", agents)
        self.assertNotIn("20. Is meaningful formatting preserved?", agents)
        self.assertNotIn("pending -> extracted -> translated -> reviewed", agents)

    def test_setup_contract_preserves_setup_guarantees(self):
        setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")

        for phrase in (
            "git checkout --detach <resolved-revision>",
            "Never overwrite a pre-existing unrelated file",
            ".book-translator/",
            ".book-translator-install.json",
            "isolated workers",
            "docs/TRANSLATION.md",
            "`scripts/corpus.py`;",
        ):
            self.assertIn(phrase, setup)

        self.assertNotIn("Do not translate multiple chapters concurrently by default", setup)
        self.assertNotIn("Reviewer worker", setup)
        self.assertNotIn("target-language literary polish", setup)

    def test_setup_contract_defines_persistent_multi_book_workspace_policy(self):
        setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8").lower()

        for phrase in (
            "choose workspace persistence",
            "persistent writable workspace",
            "multi-session",
            "multiple books",
            "books/<book-slug>/",
            "permanent branch per book",
            "optional",
            "do not claim",
            "do not use a fixed chapter-count threshold",
        ):
            self.assertIn(phrase, setup)

    def test_orchestration_contract_preserves_execution_guarantees(self):
        text = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
        lowered = text.lower()

        for phrase in (
            "Only the orchestrator may update global mutable state",
            "single_agent_bounded_context",
            "Do not translate multiple chapters concurrently by default",
            "metadata.json.workflow",
            "do not silently",
            "CORRECTIONS_REQUIRED",
            "docs/TRANSLATION.md",
            "contract_read_order",
            "context_profiles",
            "python scripts/corpus.py verify <book-slug>",
        ):
            self.assertIn(phrase.lower(), lowered)

        self.assertNotIn("20. Is meaningful formatting preserved?", text)
        self.assertNotIn("git checkout --detach <resolved-revision>", text)

    def test_translation_contract_preserves_literary_guarantees(self):
        text = (PROJECT_ROOT / "docs" / "TRANSLATION.md").read_text(encoding="utf-8")
        lowered = text.lower()

        for phrase in (
            "translator is a careful interpreter, not a co-author",
            "ambiguity",
            "subtext",
            "character voice",
            "source-comparison",
            "target-language literary polish",
            "meaningful formatting",
            "CORRECTIONS_REQUIRED",
            "PASS alone does not mutate `progress.json`",
        ):
            self.assertIn(phrase.lower(), lowered)

        self.assertNotIn("git checkout --detach <resolved-revision>", text)
        self.assertNotIn(".book-translator-install.json", text)
        self.assertNotIn("install_root", text)

    def test_book_metadata_template_records_workflow_provenance(self):
        metadata = json.loads((PROJECT_ROOT / "docs" / "templates" / "metadata.json").read_text(encoding="utf-8"))
        workflow = metadata["workflow"]

        self.assertEqual(workflow["repository"], "https://github.com/tim8es/book-translator")
        self.assertIn("requested_ref", workflow)
        self.assertIn("resolved_revision", workflow)

    def test_skill_preserves_one_link_bootstrap(self):
        skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("https://github.com/tim8es/book-translator", skill)
        self.assertIn("source book", skill.lower())
        self.assertIn("target language", skill.lower())
        self.assertIn("context_profiles", skill)


if __name__ == "__main__":
    unittest.main()
