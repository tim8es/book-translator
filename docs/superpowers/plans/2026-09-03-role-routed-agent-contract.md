# Role-Routed Agent Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the global all-documents agent contract with role-routed context profiles while preserving every existing translation-quality, review, provenance, resume, fallback, and structural guarantee.

**Architecture:** `agent-manifest.json` becomes a machine-readable router to four authoritative contracts: global invariants, setup, orchestration, and translation. `SKILL.md` becomes discovery-only; `AGENTS.md` becomes a small auto-load-safe invariant layer; `docs/TRANSLATION.md` becomes the complete literary contract; `docs/ORCHESTRATION.md` and `docs/AGENT_SETUP.md` retain only their own domains; README becomes explicitly non-normative.

**Tech Stack:** Markdown contracts, JSON manifest, Python `unittest`, existing stdlib-only `scripts/book.py`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-03-role-routed-agent-contract-design.md`

## Global Constraints

- Preserve all behavioral guarantees listed in the design spec.
- Do not weaken literary fidelity or source-comparison review criteria.
- Do not add mandatory dependencies, services, LLM SDKs, API keys, databases, queues, or backends.
- Preserve chapter states exactly: `pending`, `extracted`, `translated`, `reviewed`.
- Preserve per-install and per-book workflow provenance and no-silent-upgrade behavior.
- Preserve the `.book-translator/` collision fallback and never overwrite unrelated host files.
- Keep `scripts/book.py` behavior backward-compatible in this migration.
- Remove normative duplication rather than merely rewording it.
- README is human-facing and must not be required for execution correctness.
- This migration does not implement durable review hash evidence.

---

### Task 1: Replace duplication tests with role-routing contract tests

**Files:**
- Modify: `tests/test_agent_contract.py`

**Interfaces:**
- Consumes: current manifest and documentation paths.
- Produces: regression tests that define the new authority/routing architecture before production documents change.

- [ ] **Step 1: Replace the global `contract_read_order` test with manifest routing tests**

Add tests equivalent to:

```python
def test_manifest_routes_roles_to_minimal_contracts(self):
    manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))

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


def test_context_profiles_reference_only_declared_contracts(self):
    manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
    declared = set(manifest["contracts"])
    for profile, keys in manifest["context_profiles"].items():
        self.assertTrue(keys, msg=profile)
        self.assertTrue(set(keys) <= declared, msg=profile)
        for key in keys:
            self.assertTrue((PROJECT_ROOT / manifest["contracts"][key]).is_file(), msg=f"{profile}:{key}")
```

- [ ] **Step 2: Add tests for strict profile isolation**

```python
def test_role_profiles_exclude_irrelevant_contracts(self):
    manifest = json.loads((PROJECT_ROOT / "agent-manifest.json").read_text(encoding="utf-8"))
    profiles = manifest["context_profiles"]

    self.assertNotIn("translation", profiles["bootstrap"])
    self.assertNotIn("orchestration", profiles["bootstrap"])
    self.assertNotIn("setup", profiles["orchestrator"])
    self.assertNotIn("translation", profiles["orchestrator"])
    self.assertNotIn("setup", profiles["translator"])
    self.assertNotIn("orchestration", profiles["translator"])
    self.assertNotIn("setup", profiles["reviewer"])
    self.assertNotIn("orchestration", profiles["reviewer"])
```

- [ ] **Step 3: Add authority-boundary tests**

Add assertions that:

```python
def test_legacy_translation_guide_is_removed(self):
    self.assertFalse((PROJECT_ROOT / "docs" / "TRANSLATION_GUIDE.md").exists())
    self.assertTrue((PROJECT_ROOT / "docs" / "TRANSLATION.md").is_file())


def test_readme_is_explicitly_non_normative(self):
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    self.assertIn("not part of the agent execution contract", readme.lower())


def test_skill_is_discovery_only(self):
    skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
    self.assertIn("context_profiles", skill)
    self.assertNotIn("Translator worker", skill)
    self.assertNotIn("Reviewer worker", skill)
    self.assertNotIn("single writer", skill.lower())


def test_global_contract_stays_role_agnostic(self):
    agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    self.assertIn("context_profiles", agents)
    self.assertNotIn("git clone https://github.com/tim8es/book-translator.git", agents)
    self.assertNotIn("Stage 2 — fidelity review", agents)
    self.assertNotIn("20. Is meaningful formatting preserved?", agents)
```

- [ ] **Step 4: Add domain-preservation tests**

Add focused tests requiring the authoritative files to retain their essential guarantees without requiring those phrases elsewhere:

```python
def test_setup_contract_preserves_setup_guarantees(self):
    setup = (PROJECT_ROOT / "docs" / "AGENT_SETUP.md").read_text(encoding="utf-8")
    for phrase in (
        "git checkout --detach <resolved-revision>",
        "Never overwrite a pre-existing unrelated file",
        ".book-translator/",
        ".book-translator-install.json",
        "isolated workers",
    ):
        self.assertIn(phrase, setup)


def test_orchestration_contract_preserves_execution_guarantees(self):
    text = (PROJECT_ROOT / "docs" / "ORCHESTRATION.md").read_text(encoding="utf-8")
    for phrase in (
        "Only the orchestrator may update global mutable state",
        "single_agent_bounded_context",
        "Do not translate multiple chapters concurrently by default",
        "metadata.json.workflow",
        "do not silently",
    ):
        self.assertIn(phrase.lower(), text.lower())


def test_translation_contract_preserves_literary_guarantees(self):
    text = (PROJECT_ROOT / "docs" / "TRANSLATION.md").read_text(encoding="utf-8")
    for phrase in (
        "translator is a careful interpreter, not a co-author",
        "ambiguity",
        "subtext",
        "character voice",
        "source-comparison",
        "target-language literary polish",
        "meaningful formatting",
    ):
        self.assertIn(phrase.lower(), text.lower())
```

- [ ] **Step 5: Run the contract test and confirm RED**

Run:

```bash
python -m unittest tests.test_agent_contract -v
```

Expected: failures because `contracts`, `context_profiles`, and `docs/TRANSLATION.md` do not exist yet and legacy duplication remains.

- [ ] **Step 6: Commit the failing tests**

```bash
git add tests/test_agent_contract.py
git commit -m "test: define role-routed agent contract"
```

---

### Task 2: Convert manifest and discovery layer to role routing

**Files:**
- Modify: `agent-manifest.json`
- Modify: `SKILL.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: routing contract defined by Task 1.
- Produces: `contracts` and `context_profiles` manifest API used by every agent role; thin discovery skill; small global invariant contract.

- [ ] **Step 1: Update `agent-manifest.json`**

Change schema version to `3`. Remove `contract_read_order`, `orchestration_guide`, duplicated execution prose fields that are no longer needed as normative copies, and detailed completion prose.

Add exactly:

```json
"contracts": {
  "global": "AGENTS.md",
  "setup": "docs/AGENT_SETUP.md",
  "orchestration": "docs/ORCHESTRATION.md",
  "translation": "docs/TRANSLATION.md"
},
"context_profiles": {
  "bootstrap": ["global", "setup"],
  "orchestrator": ["global", "orchestration"],
  "translator": ["global", "translation"],
  "reviewer": ["global", "translation"]
}
```

Keep machine facts needed for repository identity, version policy, required/optional user inputs, source format capabilities, provenance locations, and install roots.

- [ ] **Step 2: Rewrite `SKILL.md` as discovery-only**

Required behavior:

```markdown
## Bootstrap

1. Preserve an explicitly pinned branch/tag/commit; otherwise use the manifest default ref.
2. Resolve that ref once when possible.
3. Read `agent-manifest.json` from that selected revision.
4. Select the `context_profiles` entry matching the current role.
5. Load only the declared contract files for that profile from the same revision.
6. Do not carry setup/orchestration contracts into Translator or Reviewer context unless transitioning roles.
```

Retain one-link invocation, canonical repository, required source book/target language discovery, capability honesty, and no-silent-revision-switch guarantee. Delete Translator/Reviewer worker procedures, worker-context lists, single-writer detail, and completion detail.

- [ ] **Step 3: Rewrite `AGENTS.md` as global invariants only**

Keep concise sections for:
- role routing through manifest;
- durable repository state over chat history;
- source immutability;
- capability honesty;
- no silent workflow revision changes;
- orchestrator ownership of shared mutable state;
- ask-only-for-missing-semantic-input behavior;
- privacy/copyright/repository hygiene;
- scope discipline/no hidden infrastructure.

Do not retain Git commands, format-specific extraction, chapter workflow, detailed literary rules, review checklist, progress schema details, resume algorithm, validation checklist, or build instructions.

- [ ] **Step 4: Run routing/discovery tests**

Run:

```bash
python -m unittest tests.test_agent_contract.AgentContractTests.test_manifest_routes_roles_to_minimal_contracts -v
python -m unittest tests.test_agent_contract.AgentContractTests.test_context_profiles_reference_only_declared_contracts -v
python -m unittest tests.test_agent_contract.AgentContractTests.test_skill_is_discovery_only -v
python -m unittest tests.test_agent_contract.AgentContractTests.test_global_contract_stays_role_agnostic -v
```

Expected: manifest/profile tests still fail until `TRANSLATION.md` exists; SKILL/AGENTS boundary tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent-manifest.json SKILL.md AGENTS.md
git commit -m "refactor: route agent contracts by role"
```

---

### Task 3: Create the authoritative literary contract and remove the legacy guide

**Files:**
- Create: `docs/TRANSLATION.md`
- Delete: `docs/TRANSLATION_GUIDE.md`

**Interfaces:**
- Consumes: literary/review substance currently in `AGENTS.md` and unique literary guidance from `TRANSLATION_GUIDE.md`.
- Produces: single authoritative contract for Translator and Reviewer roles.

- [ ] **Step 1: Create `docs/TRANSLATION.md` with a clear authority boundary**

Start with:

```markdown
# Literary translation and review contract

This file is authoritative for literary fidelity, Translator behavior, Reviewer source-comparison criteria, and literary review outcomes.

It does not control installation, worker topology, or durable state mutation. `docs/ORCHESTRATION.md` decides when a Reviewer outcome may be persisted as `status=reviewed`.
```

- [ ] **Step 2: Migrate literary fidelity rules without weakening them**

Preserve the current substance of:
- non-negotiable translation standard;
- translator-as-interpreter rule;
- translation decision priorities 1–8;
- semantic fidelity details;
- ambiguity/uncertainty/subtext rules;
- natural target-language prose rules;
- style-guide literary observations;
- glossary literary/continuity decisions;
- pre-translation scene/context analysis;
- complete translation draft requirements;
- full source-comparison review checklist;
- target-language literary polish;
- literary definition of review PASS;
- dialogue/character voice;
- re-review of changed/questioned translations.

Do not copy setup/resume/install/build/state-mutation procedure into this file.

- [ ] **Step 3: Define Reviewer output as an interface, not a state mutation**

Include:

```text
Reviewer outcome:
- PASS, optionally with accepted literary/global-state proposals; or
- CORRECTIONS_REQUIRED with concrete findings/corrections.

PASS alone does not mutate `progress.json`. The Orchestrator applies the state transition under `ORCHESTRATION.md`.
```

- [ ] **Step 4: Delete `docs/TRANSLATION_GUIDE.md` only after checking unique content was assigned**

Ensure its format guidance, state/resume detail, and validation detail are retained in setup/orchestration where still required; delete explanatory duplicates rather than moving them wholesale.

- [ ] **Step 5: Run literary and path tests**

Run:

```bash
python -m unittest tests.test_agent_contract.AgentContractTests.test_legacy_translation_guide_is_removed -v
python -m unittest tests.test_agent_contract.AgentContractTests.test_translation_contract_preserves_literary_guarantees -v
python -m unittest tests.test_agent_contract.AgentContractTests.test_manifest_routes_roles_to_minimal_contracts -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/TRANSLATION.md docs/TRANSLATION_GUIDE.md
git commit -m "docs: establish authoritative translation contract"
```

---

### Task 4: Make setup and orchestration single-purpose authorities

**Files:**
- Modify: `docs/AGENT_SETUP.md`
- Modify: `docs/ORCHESTRATION.md`

**Interfaces:**
- Consumes: global contract, manifest profile routing, literary Reviewer interface from `TRANSLATION.md`.
- Produces: setup-only technical contract and orchestration-only execution/state contract.

- [ ] **Step 1: Remove translation-loop duplication from `AGENT_SETUP.md`**

Keep:
- requested ref resolution;
- coherent revision rule;
- capability detection;
- Git/repository API/read-only branches;
- collision policy;
- deterministic runtime copy set, updated to `docs/TRANSLATION.md`;
- install provenance;
- format/helper capability behavior;
- successful-setup definition.

Replace any detailed translation loop with a single handoff such as:

```markdown
After setup, transition to the `orchestrator` context profile from `agent-manifest.json`. Setup policy is no longer part of Translator or Reviewer context.
```

Do not retain literary review or chapter sequencing detail.

- [ ] **Step 2: Consolidate state/resume semantics in `ORCHESTRATION.md`**

Keep or migrate here:
- installation root and active-book selection;
- per-book workflow revision selection;
- chapter state meanings only to the extent needed for state transitions;
- next-chapter selection;
- bounded context construction;
- isolated and single-agent role execution;
- strict single writer;
- sequential default;
- Translator artifact acceptance;
- Reviewer PASS/corrections handling;
- failure handling;
- resume algorithm;
- workflow upgrade transition;
- structural validation ordering;
- output build/completion sequencing.

For literary criteria, say only that Translator/Reviewer follow `docs/TRANSLATION.md`; do not repeat the detailed checklist.

- [ ] **Step 3: Make the review/state boundary explicit**

Add a transition equivalent to:

```text
translated artifact
  -> Reviewer under TRANSLATION.md
  -> CORRECTIONS_REQUIRED: remain translated
  -> PASS: orchestrator applies accepted corrections/proposals, validates state, then may persist reviewed
```

- [ ] **Step 4: Run setup/orchestration tests**

Run:

```bash
python -m unittest tests.test_agent_contract.AgentContractTests.test_setup_contract_preserves_setup_guarantees -v
python -m unittest tests.test_agent_contract.AgentContractTests.test_orchestration_contract_preserves_execution_guarantees -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/AGENT_SETUP.md docs/ORCHESTRATION.md
git commit -m "docs: isolate setup and orchestration authority"
```

---

### Task 5: Make README human-only and update all references

**Files:**
- Modify: `README.md`
- Modify: `tests/test_agent_contract.py` if exact final wording needs test adjustment without weakening the invariant.

**Interfaces:**
- Consumes: final authoritative contract layout.
- Produces: concise human-facing project overview with no executable-policy dependency.

- [ ] **Step 1: Rewrite the README contract section**

Near the top state clearly:

```markdown
README is a human-facing overview and is not part of the agent execution contract. Agents enter through `SKILL.md`/`agent-manifest.json` and load the role-specific context profile declared by the manifest.
```

- [ ] **Step 2: Keep only user-useful material**

Retain:
- project purpose;
- fastest-start prompt;
- supported clients and source formats;
- high-level role diagram;
- repository/book layout;
- CLI commands;
- privacy/copyright summary;
- development test command;
- links to the four authoritative contracts.

Remove normative bootstrap checklists, duplicated execution rules, duplicated resume rules, and duplicated definition-of-reviewed prose.

- [ ] **Step 3: Update every old `TRANSLATION_GUIDE.md` reference to `TRANSLATION.md`**

Search the repository and ensure no runtime/document/test reference remains except historical design/plan text that intentionally names the deleted file as a migration source.

- [ ] **Step 4: Run README/reference tests**

Run:

```bash
python -m unittest tests.test_agent_contract.AgentContractTests.test_readme_is_explicitly_non_normative -v
python -m unittest tests.test_agent_contract -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_agent_contract.py
git commit -m "docs: make README non-normative"
```

---

### Task 6: Full regression and architecture verification

**Files:**
- Verify: `agent-manifest.json`
- Verify: `SKILL.md`
- Verify: `AGENTS.md`
- Verify: `docs/AGENT_SETUP.md`
- Verify: `docs/ORCHESTRATION.md`
- Verify: `docs/TRANSLATION.md`
- Verify: `README.md`
- Verify: `scripts/book.py`
- Verify: `tests/test_agent_contract.py`
- Verify: `tests/test_book_cli.py`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: merge-ready role-routed architecture with unchanged CLI behavior.

- [ ] **Step 1: Run the full unit suite**

```bash
python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 2: Verify manifest profiles mechanically**

Use a small Python check:

```bash
python - <<'PY'
import json
from pathlib import Path
m = json.loads(Path('agent-manifest.json').read_text())
assert set(m['contracts']) == {'global', 'setup', 'orchestration', 'translation'}
assert set(m['context_profiles']) == {'bootstrap', 'orchestrator', 'translator', 'reviewer'}
for profile, keys in m['context_profiles'].items():
    for key in keys:
        assert key in m['contracts'], (profile, key)
        assert Path(m['contracts'][key]).is_file(), (profile, key)
print('role routing: valid')
PY
```

Expected: `role routing: valid`.

- [ ] **Step 3: Verify legacy runtime references are gone**

```bash
git grep -n "docs/TRANSLATION_GUIDE.md\|contract_read_order" -- ':!docs/superpowers/specs/*' ':!docs/superpowers/plans/*'
```

Expected: no output.

- [ ] **Step 4: Verify role contamination is absent**

```bash
git grep -n "git clone https://github.com/tim8es/book-translator.git" -- AGENTS.md SKILL.md docs/TRANSLATION.md
git grep -n "Stage 2 — fidelity review\|20. Is meaningful formatting preserved" -- AGENTS.md SKILL.md docs/AGENT_SETUP.md docs/ORCHESTRATION.md
```

Expected: no output. Git clone details belong only in setup; the detailed literary review checklist belongs only in translation.

- [ ] **Step 5: Review the final diff against the design spec**

Confirm every behavioral guarantee in the design section `Existing behavioral guarantees that must survive migration` still has one authoritative home and is either tested directly or covered by an existing CLI test.

- [ ] **Step 6: Commit any verification-only corrections**

If verification requires corrections, make the smallest changes and commit them with:

```bash
git add -A
git commit -m "test: finalize role-routed contract verification"
```

Do not create an empty commit when no correction is needed.

- [ ] **Step 7: Open a pull request to `main` and require CI green before merge**

PR summary must state:
- global `contract_read_order` replaced by role-specific context profiles;
- `AGENTS.md`/`SKILL.md` reduced to global/discovery responsibilities;
- literary contract moved to `docs/TRANSLATION.md`;
- setup/orchestration authority isolated;
- README made non-normative;
- legacy `TRANSLATION_GUIDE.md` removed;
- no CLI behavior change;
- future review-hash evidence remains out of scope.

- [ ] **Step 8: Merge only after both Python matrix jobs pass**

After merge, verify the push-triggered `main` workflow also passes before declaring completion.
