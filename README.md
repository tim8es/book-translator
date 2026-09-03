# Book Translator

**Agent-native workflow for translating full books with durable state, independent source-comparison review, and role-routed AI context.**

Book Translator is designed primarily for **ChatGPT Web** and **Codex**, while remaining **agent-agnostic** so other capable AI agents can use the same repository contract.

> Repository: `https://github.com/tim8es/book-translator`

> **README is a human-facing overview and is not part of the agent execution contract.** The executable routing source is `agent-manifest.json`; role-specific rules live in the contracts referenced there.

## Fastest start

Give an AI agent:

1. this repository URL;
2. the source book;
3. the target language.

For example:

```text
Use https://github.com/tim8es/book-translator to translate this book into Russian.
Set up everything you can automatically and follow the repository contract.
```

The user does not need to explain repository structure, worker topology, chapter state, Python helpers, glossary handling, or the review protocol.

## Why the contract is role-routed

A full-book translation needs several different kinds of reasoning. Loading every setup, orchestration, literary, and state-management rule into every worker wastes context and increases the chance that responsibilities become mixed.

Book Translator therefore routes each role to the smallest authoritative instruction set it needs:

```text
agent-manifest.json
       │
       ├─ bootstrap    → AGENTS.md + docs/AGENT_SETUP.md
       ├─ orchestrator → AGENTS.md + docs/ORCHESTRATION.md
       ├─ translator   → AGENTS.md + docs/TRANSLATION.md
       └─ reviewer     → AGENTS.md + docs/TRANSLATION.md
```

`AGENTS.md` contains only global invariants that apply to every role. Setup instructions do not travel into translation context, and detailed literary review criteria do not travel into setup context.

## Contract responsibilities

| File | Purpose |
| --- | --- |
| `agent-manifest.json` | Machine-readable contract registry, context profiles, stable defaults, version/provenance locations and source-format capabilities. |
| `SKILL.md` | Thin one-link discovery/bootstrap entrypoint. |
| `AGENTS.md` | Small global invariant layer safe to auto-load. |
| `docs/AGENT_SETUP.md` | Ref/version resolution, capability detection, installation, collision handling and install provenance. |
| `docs/ORCHESTRATION.md` | Book initialization/resume, role dispatch, bounded context, chapter state, single-writer persistence, validation and completion sequencing. |
| `docs/TRANSLATION.md` | Authoritative literary translation and independent source-comparison review contract. |

Each substantive rule has one intended human-readable authority rather than several competing copies.

## Translation quality model

The literary contract is fidelity-first. It covers meaning, factual and causal accuracy, ambiguity, subtext, authorial voice, character voice, rhythm, tone, register, repetition, formatting and natural target-language prose.

Translation and review are separate roles. The Reviewer compares the source with the current translation and returns a literary outcome; the Orchestrator owns durable state changes. This separation avoids treating fluent prose or a worker's self-assessment as proof of fidelity.

See [`docs/TRANSLATION.md`](docs/TRANSLATION.md) for the authoritative quality contract.

## Durable and resumable state

A real book lives in its own workspace:

```text
books/<book-slug>/
├── source/         # preserved original
├── extracted/      # source chapters/sections
├── translated/     # translated chapters
├── output/         # assembled deliverables
├── metadata.json   # book metadata + workflow provenance
├── progress.json   # durable chapter queue
├── glossary.md     # recurring lexical/continuity decisions
└── style-guide.md  # evidence-based literary observations
```

The repository state is designed so another session can resume the book without relying on previous chat history. Each book records its workflow provenance, allowing different books in one workspace to remain associated with the revisions under which they were initialized.

## Execution model

When independent workers or subagents are available, the normal high-level flow is:

```text
Orchestrator
  → Translator
  → Reviewer
  → Orchestrator accepts valid state
  → next chapter
```

When isolated workers are unavailable, the same logical roles can be executed sequentially with bounded context in one agent session. The detailed execution and state-transition contract is in [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md).

## Setup and integration

Book Translator can run as its own repository or be integrated into another writable repository/workspace. The setup contract handles revision pinning, capability detection, installation provenance and safe collision behavior.

When a host repository already uses paths such as `AGENTS.md`, Book Translator has a namespaced `.book-translator/` installation mode rather than requiring unrelated host files to be replaced.

See [`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md).

## Supported source formats

The included standard-library Python helper can automatically extract:

- EPUB;
- HTML / XHTML;
- Markdown;
- TXT.

DOCX and PDF can use the same durable workflow when the active agent can read or extract them reliably, but the included helper does not claim automatic extraction for those formats.

For EPUB, the helper follows package/spine reading order.

## Optional local CLI

`scripts/book.py` performs structural work only. It does not call an LLM API and does not replace literary review.

Typical commands:

```bash
python scripts/book.py extract /path/to/book.epub --target-language ru
python scripts/book.py validate <book-slug>
python scripts/book.py build <book-slug>
```

The default workflow has no mandatory external Python dependencies, database, backend, queue, or LLM API key.

## Project structure

```text
.
├── agent-manifest.json       # role/context router
├── SKILL.md                  # discovery entrypoint
├── AGENTS.md                 # global invariants
├── books/                    # per-book durable state
├── docs/
│   ├── AGENT_SETUP.md        # technical setup authority
│   ├── ORCHESTRATION.md      # execution/state authority
│   ├── TRANSLATION.md        # literary translation/review authority
│   └── templates/            # state templates
├── scripts/
│   └── book.py               # optional structural helper
└── tests/
    ├── test_agent_contract.py
    └── test_book_cli.py
```

Design and implementation records for major architectural changes may also live under `docs/superpowers/`; they document development decisions and are not runtime contracts.

## Tests

Run the complete test suite with:

```bash
python -m unittest discover -s tests -v
```

Contract tests protect role routing and authority boundaries; CLI tests protect extraction, provenance, validation and build behavior.

## Privacy and copyright

The canonical repository contains the workflow, not bundled real books.

Do not publish copyrighted source material or private translations unless you have the right to do so. A private repository or workspace is appropriate when the source or resulting translation should remain private.

## License

MIT. See [`LICENSE`](LICENSE).
