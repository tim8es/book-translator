# Book Translator

**Agent-native workflow for translating full books with persistent state, literary fidelity checks, resumable review, and bounded-context orchestration.**

Book Translator is designed primarily for **ChatGPT Web** and **Codex**, while remaining **agent-agnostic**: other capable AI agents can follow the same repository contract when they can read the repository and access the source book.

The repository is meant to be handed directly to an AI agent. The agent should inspect the repository instructions, set up everything it can by itself, ask only for genuinely missing user inputs, and manage the translation as durable file-based state.

> Repository URL: `https://github.com/tim8es/book-translator`

## Fastest start

Give your AI agent:

1. this repository URL;
2. the source book file;
3. the target language.

That is enough for the normal workflow.

```text
Use https://github.com/tim8es/book-translator to translate this book into Russian.
Set up everything you can automatically, follow the repository instructions,
and continue until the translation is reviewed.
```

If you provide only the repository URL first, the agent should inspect [`agent-manifest.json`](agent-manifest.json), [`SKILL.md`](SKILL.md), and [`AGENTS.md`](AGENTS.md), then ask only for required inputs it cannot infer or access.

You do **not** need to explain the repository structure, request subagents manually, copy prompts, install an LLM SDK, create a database, or configure an API key for the default agent-driven workflow.

> Experimental branch note: when testing a non-`main` version, give the agent the branch URL or explicitly pin that branch. A bare repository URL intentionally resolves the default/latest `main` contract.

## Clients and compatibility

### ChatGPT Web

Use the repository with whatever repository/file/workspace capabilities are actually available in the active ChatGPT environment. The workflow must not assume shell, Git, or independent subagents when those capabilities are unavailable.

When isolated workers are unavailable, the same translation/review roles run sequentially with bounded reloaded context.

### Codex

Codex is the most automated path when it has a writable workspace: it can clone/open the repository, run the optional Python helper, persist state, validate files, use Git, and use isolated worker sessions when supported.

### Other capable AI agents

The workflow is intentionally agent-agnostic. Another AI agent can use Book Translator when it can at minimum:

- read the repository instructions;
- access the source book;
- produce the target-language text;
- persist or return the workflow state needed to continue.

Filesystem, shell, Git, repository API, Python, and subagent capabilities increase automation but are not requirements of the literary method itself.

## Orchestrated chapter workflow

When isolated workers/subagents are available, Book Translator prefers a fresh context for translation and a separate fresh context for review:

```text
Orchestrator
  -> Chapter N Translator (fresh worker)
  -> Chapter N Reviewer   (fresh independent worker)
  -> Orchestrator validates + commits global state
  -> Chapter N+1
```

The orchestrator is the only writer of global mutable state such as `progress.json`, `glossary.md`, and `style-guide.md`. Workers may propose changes, but they should not race to update shared state.

Chapters are sequential by default:

```text
T1 -> R1 -> state commit -> T2 -> R2 -> state commit -> ...
```

This protects book-wide terminology, character voice, ambiguity, and continuity. Parallel chapter translation is deliberately not the default.

If isolated workers are unavailable, the same agent executes Translator and Reviewer as separate bounded-context roles. Translation and review must still remain separate passes.

See [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md).

## What the agent should do automatically

When given this repository, a capable agent should:

1. read `agent-manifest.json`, `SKILL.md`, and `AGENTS.md`;
2. resolve the requested ref, or latest `main` when none is pinned;
3. resolve that ref once to a concrete revision when possible and record it in a writable project;
4. inspect its own capabilities and choose the documented execution mode/fallback;
5. identify the source book and target language from the conversation/files;
6. ask only for required information that is actually missing;
7. create or resume a per-book workspace under `books/<book-slug>/`;
8. preserve the original source unchanged;
9. extract chapters in real reading order;
10. initialize translation metadata, progress, glossary, and style guide;
11. translate chapter by chapter;
12. perform a separate source-to-translation fidelity review before marking a chapter `reviewed`;
13. persist progress so another agent/session can resume without chat history;
14. validate repository state and build the requested output.

The authoritative literary behavior and quality rules are in [`AGENTS.md`](AGENTS.md). Execution topology and context boundaries are in [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md).

## Deterministic operational state

Book Translator cannot make literary generation itself deterministic, but it can make the **process** substantially more deterministic.

Operational rules include:

- resolve `latest main` once for an active setup instead of silently changing versions mid-book;
- record the resolved workflow revision when possible in `.book-translator-install.json`;
- select chapters from durable `progress.json` state;
- keep shared state single-writer through the orchestrator;
- use explicit Translator and Reviewer roles;
- give workers bounded task-specific context rather than an ever-growing chat history;
- do not choose arbitrarily between multiple incomplete books.

## Required inputs

Only two inputs are fundamentally required:

| Input | Required | Notes |
| --- | --- | --- |
| Source book | Yes | File or content the agent can access. |
| Target language | Yes | For example `ru`, `en`, `de`, or a language name. |
| Source language | Usually no | Detect when possible. |
| Title / author | No | Read from the source when possible. |
| Output format | No | Markdown is the safe default. |

The agent should not ask the user to choose technical defaults that it can select safely itself.

## Supported source formats

The included optional Python helper can automatically extract:

- EPUB;
- HTML / XHTML;
- Markdown;
- TXT.

DOCX and PDF can still use the same repository workflow when the active agent can read them reliably, but automatic extraction is not claimed by the included helper.

For EPUB, reading order comes from the EPUB package/spine rather than from blindly treating every XHTML file as a chapter.

## Project structure

```text
.
├── SKILL.md                  # portable discovery/bootstrap layer
├── AGENTS.md                 # authoritative agent + translation rules
├── agent-manifest.json       # machine-readable bootstrap/execution contract
├── books/                    # user book workspaces; template is empty
├── docs/
│   ├── AGENT_SETUP.md        # capability-aware setup/bootstrap behavior
│   ├── ORCHESTRATION.md      # worker roles, context bounds, single-writer rules
│   ├── TRANSLATION_GUIDE.md  # repository state and workflow reference
│   └── templates/            # per-book state templates
├── scripts/
│   └── book.py               # optional stdlib-only extraction/validation/build helper
└── tests/
    ├── test_agent_contract.py
    └── test_book_cli.py
```

Each real book gets:

```text
books/<book-slug>/
├── source/         # immutable original
├── extracted/      # original chapters/sections
├── translated/     # translated chapters
├── output/         # assembled deliverables
├── metadata.json
├── progress.json
├── glossary.md
└── style-guide.md
```

## Resume without chat history

`progress.json` is the durable work queue:

```text
pending -> extracted -> translated -> reviewed
```

A new session should reconstruct work from repository state, not from remembered chat history. If exactly one incomplete book exists, it can be resumed automatically. If several incomplete books exist and user intent does not identify one, the agent should ask only which book to resume.

`reviewed` has a strict meaning: the translation was systematically compared against the original for completeness, meaning, tone, ambiguity, voice, rhythm, and meaningful formatting. Fluent prose alone is not enough.

## Optional local CLI

The default workflow does not require local software beyond the agent itself. If Python 3 is available, the helper can automate structural work:

```bash
python scripts/book.py extract /path/to/book.epub --target-language ru
python scripts/book.py validate <book-slug>
python scripts/book.py build <book-slug>
```

The helper uses only the Python standard library. It does not call an LLM API and is not required for translation or literary review.

Run tests with:

```bash
python -m unittest discover -s tests -v
```

## Privacy and copyright

This public repository contains the workflow, not bundled books.

Do not publish copyrighted source books or private translations unless you have the right to do so. If the source material should remain private, use this repository as a template in a private workspace/repository.

The canonical template must never copy example or user book contents from another workspace into `books/`.

## Design principles

- **One-link:** the repository URL is enough to discover the workflow.
- **Agent-agnostic:** ChatGPT Web and Codex are primary scenarios, not hard dependencies.
- **Bounded context:** workers receive only task-relevant context.
- **Independent review:** translation does not approve itself when isolation is available.
- **Single writer:** the orchestrator owns shared mutable state.
- **File-based:** state stays transparent and inspectable.
- **Resumable:** another session can continue from repository state.
- **Fidelity-first:** the translator is an interpreter, not a co-author.
- **Capability-aware:** automate what the agent can do; explain only the unavoidable manual step.
- **No hidden infrastructure:** no database, backend, queue, or mandatory LLM API integration.

## Documentation

- [`SKILL.md`](SKILL.md) — portable skill/bootstrap entrypoint.
- [`AGENTS.md`](AGENTS.md) — authoritative execution and literary-quality rules.
- [`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md) — what an agent should do when it receives only the repository URL.
- [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md) — isolated workers, bounded context, fallbacks, and single-writer state.
- [`docs/TRANSLATION_GUIDE.md`](docs/TRANSLATION_GUIDE.md) — repository state, formats, resume flow, and validation.

## License

MIT. See [`LICENSE`](LICENSE).
