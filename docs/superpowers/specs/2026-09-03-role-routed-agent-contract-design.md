# Role-Routed Agent Contract Design

## Goal

Improve Book Translator's execution quality for AI agents by reducing irrelevant instruction load, eliminating competing normative copies, and routing each agent role to the smallest authoritative contract needed for that role without weakening translation fidelity, review, resumability, provenance, or fallback behavior.

## Problem

The current contract is internally consistent, but it requires an agent to read a broad global instruction set before useful work. `AGENTS.md` also combines bootstrap, installation, provenance, literary quality, chapter workflow, review, resume, validation, build, and completion rules. `SKILL.md`, `README.md`, `agent-manifest.json`, `AGENT_SETUP.md`, `ORCHESTRATION.md`, and `TRANSLATION_GUIDE.md` repeat portions of the same rules.

The result is avoidable context cost and role contamination: a Translator can receive Git/setup rules; a Reviewer can receive orchestration/setup rules; a bootstrap agent can receive the full literary contract. Repetition also creates future drift risk even when all copies are currently aligned.

## Design principles

1. **One authority per rule.** A normative rule has one authoritative human-readable source. Other files may point to it but must not restate it normatively.
2. **Role-routed context.** An agent loads the global invariants plus the role-specific contract needed for the current task, not every contract document.
3. **Quality is preserved, not compressed away.** Literary fidelity and review criteria remain detailed; only irrelevant instructions are removed from a worker's context.
4. **Repository state remains durable authority.** Chat history is never required to resume work when repository state is available.
5. **Workflow revision remains pinned per book.** Existing provenance and no-silent-upgrade guarantees remain unchanged.
6. **Single writer remains strict.** Orchestrator-owned shared state remains a hard invariant.
7. **Fallbacks preserve role boundaries.** If isolated workers are unavailable, Translator and Reviewer remain separate bounded-context roles in one session.
8. **README is not executable policy.** Human-facing documentation cannot be required for execution correctness.
9. **SKILL is discovery only.** It enters the repository contract but does not duplicate role behavior.

## Target structure

```text
README.md
SKILL.md
AGENTS.md
agent-manifest.json

docs/
├── AGENT_SETUP.md
├── ORCHESTRATION.md
├── TRANSLATION.md
├── templates/
│   ├── glossary.md
│   ├── metadata.json
│   ├── progress.json
│   └── style-guide.md
└── superpowers/
    ├── specs/
    └── plans/

scripts/
└── book.py

tests/
├── test_agent_contract.py
└── test_book_cli.py
```

`docs/TRANSLATION_GUIDE.md` is replaced by `docs/TRANSLATION.md`. Unique useful content is migrated before deletion.

## Authority map

### `agent-manifest.json`

Machine-readable router and stable machine facts only.

Authoritative for:
- canonical repository identity;
- default workflow ref;
- contract file registry;
- role/context profile routing;
- required/optional semantic inputs;
- stable machine defaults that are genuinely consumed programmatically or used for capability routing;
- provenance locations.

It must not duplicate detailed literary, review, orchestration, collision, resume, or completion prose.

### `SKILL.md`

Portable discovery/bootstrap entrypoint only.

Authoritative for no domain rules. It:
1. identifies the canonical repository;
2. preserves a user-pinned ref or selects the manifest default;
3. reads `agent-manifest.json` from that ref/revision;
4. selects the context profile for the current role;
5. follows that profile.

It must not define Translator/Reviewer behavior, single-writer rules, chapter sequencing, review criteria, collision policy, or completion semantics.

### `AGENTS.md`

Small global invariant contract, safe to auto-load.

Authoritative for invariants that apply to every role:
- repository state outranks chat history when durable state exists;
- source content must not be overwritten;
- unavailable capabilities must not be claimed;
- workflow revisions must not change silently during an active book run;
- workers must load only the context profile required for their current role;
- shared mutable state is orchestrator-owned;
- user should be asked only for genuinely missing semantic inputs or safety-critical ambiguity;
- copyrighted/private source material must not be published merely because the workflow repository is accessible.

It must not contain Git clone instructions, format extraction rules, detailed literary translation standards, review checklist, chapter state transition procedure, resume algorithm, validation checklist, or build procedure.

### `docs/AGENT_SETUP.md`

Technical setup contract.

Authoritative for:
- requested ref resolution and concrete revision pinning;
- capability detection;
- Git/repository API/read-only setup paths;
- standalone vs host-repository installation;
- collision policy and `.book-translator/` fallback;
- runtime file installation set;
- installation provenance;
- helper availability and installation-time limitations.

It must not define Translator/Reviewer behavior, literary fidelity, review pass criteria, chapter sequencing, or `reviewed` meaning.

### `docs/ORCHESTRATION.md`

Orchestrator execution contract.

Authoritative for:
- selecting/resuming the active book;
- determining the book's workflow revision;
- selecting the next chapter;
- building role-specific context packs;
- isolated-worker and single-agent bounded-context execution topology;
- Translator then Reviewer sequencing;
- single-writer state transitions;
- failure handling;
- state commit rules;
- resume sequencing;
- validation/build ordering;
- explicit workflow upgrade transition;
- chapter parallelism policy.

It must not restate the detailed literary translation standard or review checklist. It consumes Reviewer outcomes defined by `TRANSLATION.md`.

### `docs/TRANSLATION.md`

Literary translation and review contract.

Authoritative for:
- fidelity priorities;
- meaning, causality, chronology, negation, modality, ambiguity, subtext, tone, rhythm, register, voice, repetition, formatting, and natural target-language prose;
- glossary and style-guide usage from a literary perspective;
- Translator task requirements and expected output;
- Reviewer source-comparison requirements and review checklist;
- target-language literary polish;
- what constitutes a literary review PASS or requested corrections;
- re-review standards for changed or questioned translations.

It must not decide when repository state is persisted. A Reviewer can return PASS; only the Orchestrator may turn a valid reviewed artifact into durable `status=reviewed` state.

### `README.md`

Human-facing overview only. It may explain concepts and link to contracts but is explicitly non-normative for agent execution.

## Role-routed manifest model

Replace the single global `contract_read_order` with a contract registry and context profiles.

Required semantic shape:

```json
{
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
}
```

The manifest itself is always read first. A role then loads only the files referenced by its profile. A role may load another contract only when it is explicitly transitioning roles or resolving a documented dependency.

## Execution flows

### New installation / bootstrap

```text
repository/ref
  -> agent-manifest.json
  -> profile=bootstrap
  -> AGENTS.md + AGENT_SETUP.md
  -> resolve revision
  -> install runtime
  -> record install provenance
```

After setup, bootstrap instructions are no longer carried into translation worker context.

### Orchestrator

```text
agent-manifest.json
  -> profile=orchestrator
  -> AGENTS.md + ORCHESTRATION.md
  -> identify book + workflow revision
  -> read metadata/progress/global state
  -> create Translator context
  -> create Reviewer context
  -> commit accepted state
```

### Translator

```text
agent-manifest.json
  -> profile=translator
  -> AGENTS.md + TRANSLATION.md
  -> metadata relevant to literary task
  -> glossary
  -> style guide
  -> chapter source
  -> bounded continuity
  -> translation artifact + proposals/warnings
```

The Translator must not receive `AGENT_SETUP.md` or `ORCHESTRATION.md` by default.

### Reviewer

```text
agent-manifest.json
  -> profile=reviewer
  -> AGENTS.md + TRANSLATION.md
  -> source
  -> translation
  -> glossary
  -> style guide
  -> bounded continuity
  -> PASS or corrections/findings + proposed global decisions
```

The Reviewer does not mutate shared state.

## Review/state boundary

`TRANSLATION.md` defines review quality and the Reviewer result.

`ORCHESTRATION.md` defines state persistence.

Required boundary:

```text
Reviewer PASS
  + complete translation artifact
  + orchestrator acceptance
  + valid repository state
      -> status=reviewed
```

`reviewed` must never be used merely because prose looks fluent.

## Existing behavioral guarantees that must survive migration

The refactor must preserve all of the following:

- source and translated content remain separated;
- original source stays immutable;
- source reading order is preserved;
- only source book + target language are fundamentally required from the user;
- technical defaults are selected automatically when safe;
- explicitly pinned branch/tag/commit is preserved;
- selected ref is resolved once when possible and held stable during an active run;
- per-install and per-book provenance remain recorded;
- existing books use their recorded workflow revision unless explicitly upgraded;
- multiple books may retain different workflow revisions;
- no fabricated commit SHA;
- collision policy never overwrites unrelated host files;
- `.book-translator/` remains the deterministic namespace fallback;
- Python remains optional;
- no mandatory LLM SDK/API key/database/backend/queue;
- EPUB uses package/spine reading order;
- helper-supported formats remain EPUB, HTML/XHTML, Markdown, TXT;
- DOCX/PDF remain agent-dependent unless helper support is actually implemented;
- chapters remain sequential by default;
- isolated Translator and Reviewer workers are preferred when available;
- single-agent fallback preserves separate bounded roles;
- Reviewer does not inherit Translator hidden reasoning;
- Orchestrator is the only writer of shared mutable state;
- statuses remain `pending`, `extracted`, `translated`, `reviewed`;
- `translated` means an artifact exists but source-comparison review is incomplete;
- literary review must compare source and translation;
- significant changes after review require re-review;
- structural validation is not a substitute for literary review;
- output build is not proof of review/completion;
- the agent must not claim an artifact/capability that was not actually produced/available;
- repository state remains sufficient for resume without chat history.

## Migration of current content

### Move from `AGENTS.md` to `TRANSLATION.md`

Move, preserving substance:
- non-negotiable translation standard;
- Translator role;
- translation decision priorities;
- semantic fidelity;
- ambiguity/uncertainty/subtext;
- natural target-language prose;
- literary use of style guide and glossary;
- before-translating literary checks;
- translation draft requirements;
- source-comparison review checklist;
- target-language literary polish;
- literary definition of review PASS;
- dialogue and character voice;
- re-review guidance.

### Move from `AGENTS.md` to `ORCHESTRATION.md`

Move or consolidate:
- project/book state flow where it governs orchestration;
- adding/resuming workflow sequencing;
- `progress.json` state-transition rules;
- workflow provenance resume behavior;
- structural validation sequencing;
- build/completion sequencing.

Do not duplicate literary details moved to `TRANSLATION.md`.

### Move from `TRANSLATION_GUIDE.md`

Preserve unique repository-state or format information in the appropriate authority:
- setup/install-specific information -> `AGENT_SETUP.md`;
- orchestration/resume/state information -> `ORCHESTRATION.md`;
- literary information -> `TRANSLATION.md`;
- purely explanatory duplicates -> remove.

Then delete `docs/TRANSLATION_GUIDE.md`.

## Tests and enforcement

Tests must enforce architecture, not phrases copied across documents.

Required contract tests:

1. manifest declares exactly the four authoritative contracts and the four required context profiles;
2. every profile references only declared contract keys;
3. `bootstrap` includes global+setup and excludes orchestration+translation;
4. `orchestrator` includes global+orchestration and excludes setup+translation;
5. `translator` and `reviewer` include global+translation and exclude setup+orchestration;
6. all declared contract paths exist;
7. `docs/TRANSLATION_GUIDE.md` no longer exists;
8. README explicitly states it is not part of the execution contract;
9. SKILL references manifest/context profiles but does not define orchestration worker rules;
10. AGENTS references context profiles and remains free of setup clone commands and detailed review checklist language;
11. setup retains collision, revision pinning, provenance, and capability fallback guarantees;
12. orchestration retains single writer, sequential chapter policy, role separation, resume, failure handling, and no-silent-upgrade behavior;
13. translation retains fidelity priorities, independent source comparison, ambiguity/subtext, character voice, and literary polish requirements;
14. existing CLI provenance and structural tests continue to pass.

Tests should avoid forcing the same normative sentence to appear in multiple files.

## Non-goals

This change does not:
- add a database, service, queue, web UI, or external orchestration runtime;
- change source extraction algorithms;
- add DOCX/PDF automatic helper extraction;
- change the existing chapter state names;
- implement durable review hash evidence yet;
- add parallel chapter translation;
- change copyright/privacy policy.

Durable review evidence based on source/translation hashes is a separate hardening task after this architecture lands.

## Success criteria

The migration is complete when:

1. an AI worker can determine its role-specific contract from the manifest without loading every document;
2. each normative domain has one authoritative human-readable file;
3. `AGENTS.md` and `SKILL.md` are materially smaller and contain no detailed role-specific workflow duplication;
4. `README.md` is explicitly non-normative;
5. `TRANSLATION.md` contains the complete literary fidelity/review contract;
6. `ORCHESTRATION.md` contains the complete state/execution contract but not the literary review checklist;
7. `AGENT_SETUP.md` contains installation/version/collision/capability behavior but not translation workflow;
8. legacy `TRANSLATION_GUIDE.md` is removed after unique content migration;
9. tests enforce role routing and authority boundaries;
10. all existing CLI behavior and tests remain green.
