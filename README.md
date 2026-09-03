# Book Translator

**Agent-native workflow for translating full books with persistent state, literary fidelity checks, resumable review, and bounded-context orchestration.**

Book Translator is designed primarily for **ChatGPT Web** and **Codex**, while remaining **agent-agnostic**: other capable AI agents can follow the same repository contract when they can read the repository and access the source book.

The repository is meant to be handed directly to an AI agent. The agent should set up everything it can itself, ask only for genuinely missing user inputs, and manage the translation as durable file-based state.

> Repository URL: `https://github.com/tim8es/book-translator`

## Fastest start

Give your AI agent:

1. this repository URL;
2. the source book file;
3. the target language.

```text
Use https://github.com/tim8es/book-translator to translate this book into Russian.
Set up everything you can automatically, follow the repository instructions,
and continue until the translation is reviewed.
```

You do **not** need to explain repository structure, request subagents manually, copy prompts, install an LLM SDK, create a database, or configure an API key for the default workflow.

## Canonical contract

`agent-manifest.json` is the machine-readable bootstrap contract. Its `contract_read_order` is the single canonical instruction-loading order.

A capable agent should:

1. preserve an explicitly pinned branch/tag/commit, otherwise use the manifest default ref (`main`);
2. resolve that ref once to a concrete revision when possible;
3. read all contract files from that same revision according to `contract_read_order`;
4. select a writable installation root without overwriting unrelated host files;
5. record installation provenance;
6. initialize or resume a book workspace;
7. follow the chapter translation/review workflow until the requested scope is validated.

Do not silently switch workflow revisions during an active book run.

When integrating into an existing repository, see [`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md). A conflicting host file must never be overwritten automatically; the deterministic fallback is a namespaced `.book-translator/` installation.

## Clients and compatibility

### ChatGPT Web

Use whatever repository/file/workspace capabilities are actually available. Do not assume shell, Git, or isolated workers when they are unavailable.

When isolated workers are unavailable, Translator and Reviewer still run as separate bounded-context roles.

### Codex

Codex is the most automated path when it has a writable workspace: it can clone/open the repository, run the optional Python helper, persist state, validate files, use Git, and use isolated worker sessions when supported.

### Other capable AI agents

Another agent can use Book Translator when it can at minimum:

- read the repository contract;
- access the source book;
- produce the target-language text;
- persist or return enough durable state to resume.

Filesystem, shell, Git, repository API, Python, and worker capabilities increase automation but are not requirements of the literary method itself.

## Orchestrated chapter workflow

When isolated workers/subagents are available:

```text
Orchestrator
  -> Chapter N Translator (fresh worker)
  -> Chapter N Reviewer   (fresh independent worker)
  -> Orchestrator validates + commits global state
  -> Chapter N+1
```

The orchestrator is the only writer of global mutable state such as `progress.json`, `glossary.md`, and `style-guide.md`.

Chapters are sequential by default:

```text
T1 -> R1 -> state commit -> T2 -> R2 -> state commit -> ...
```

Parallel chapter translation is deliberately not the default because terminology, character voice, ambiguity, and continuity decisions can depend on earlier reviewed chapters.

See [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md).

## What the agent should do automatically

A capable agent should:

1. resolve and pin the workflow revision;
2. load the canonical contract through `agent-manifest.json.contract_read_order`;
3. inspect its capabilities and choose the documented execution mode/fallback;
4. identify the source book and target language from available context/files;
5. ask only for required information that is genuinely missing;
6. create or resume a per-book workspace;
7. preserve the original source unchanged;
8. extract chapters in real reading order;
9. initialize metadata, progress, glossary, and style guide;
10. record book-specific workflow provenance in `metadata.json.workflow`;
11. translate chapter by chapter;
12. perform a separate source-to-translation fidelity review before marking a chapter `reviewed`;
13. persist state so another session can resume without chat history;
14. validate state and build the requested output.

The authoritative literary behavior and quality rules are in [`AGENTS.md`](AGENTS.md). Bootstrap is in [`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md). Execution topology is in [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md).

## Deterministic operational state

Literary generation is not deterministic, but the process can be substantially more reproducible:

- resolve the selected workflow ref once;
- keep all contract files on the same resolved revision;
- record the installed revision in `.book-translator-install.json`;
- record each book's own workflow revision in `metadata.json.workflow`;
- select chapters from `progress.json`;
- keep global state single-writer through the orchestrator;
- use explicit Translator and Reviewer roles;
- give workers bounded task-specific context;
- never choose arbitrarily between multiple incomplete books.

A workspace may contain books initialized under different workflow revisions. Book-level provenance controls resume behavior; do not silently upgrade a book because the currently installed workflow is newer.

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

DOCX and PDF can use the same repository workflow when the active agent can read them reliably, but automatic extraction is not claimed by the included helper.

For EPUB, reading order comes from the EPUB package/spine.

## Project structure

```text
.
├── SKILL.md                  # portable discovery/bootstrap layer
├── AGENTS.md                 # authoritative literary + durable-state rules
├── agent-manifest.json       # machine-readable bootstrap/execution contract
├── books/                    # per-book workspaces
├── docs/
│   ├── AGENT_SETUP.md        # version resolution, installation, capabilities
│   ├── ORCHESTRATION.md      # worker roles, context bounds, single-writer rules
│   ├── TRANSLATION_GUIDE.md  # repository state and resume reference
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
├── metadata.json   # includes workflow provenance
├── progress.json
├── glossary.md
└── style-guide.md
```

For a namespaced installation in an existing repository, the same structure lives under `.book-translator/`.

## Resume without chat history

`progress.json` is the durable work queue:

```text
pending -> extracted -> translated -> reviewed
```

A new session reconstructs work from repository state. If exactly one incomplete book exists, it can be resumed automatically. If several incomplete books exist and user intent does not identify one, the agent asks only which book to resume.

Before resuming a book, use `metadata.json.workflow.resolved_revision` to determine the workflow contract that belongs to that book.

`reviewed` means the translation was systematically compared against the original for completeness, meaning, tone, ambiguity, voice, rhythm, and meaningful formatting. Fluent prose alone is not enough.

## Optional local CLI

If Python 3 is available, the helper can automate structural work:

```bash
python scripts/book.py extract /path/to/book.epub --target-language ru
python scripts/book.py validate <book-slug>
python scripts/book.py build <book-slug>
```

Use `.book-translator/scripts/book.py` for a namespaced installation.

The helper uses only the Python standard library. It does not call an LLM API and does not replace literary review.

Run tests with:

```bash
python -m unittest discover -s tests -v
```

## Privacy and copyright

The canonical repository contains the workflow, not bundled real books.

Do not publish copyrighted source books or private translations unless you have the right to do so. Use a private working repository/workspace when source material should remain private.

The canonical template must never copy example or user book contents from another workspace into `books/`.

## Design principles

- **One-link:** the repository URL is enough to discover the workflow.
- **One contract order:** `contract_read_order` prevents divergent bootstrap sequences.
- **Agent-agnostic:** ChatGPT Web and Codex are primary scenarios, not hard dependencies.
- **Bounded context:** workers receive only task-relevant context.
- **Independent review:** translation does not approve itself when isolation is available.
- **Single writer:** the orchestrator owns shared mutable state.
- **Per-book provenance:** each book records the workflow revision it belongs to.
- **File-based:** state stays transparent and inspectable.
- **Resumable:** another session can continue from repository state.
- **Fidelity-first:** the translator is an interpreter, not a co-author.
- **Capability-aware:** automate what the agent can do; explain only unavoidable manual steps.
- **No hidden infrastructure:** no database, backend, queue, or mandatory LLM API integration.

## Documentation

- [`agent-manifest.json`](agent-manifest.json) — canonical machine-readable contract and `contract_read_order`.
- [`SKILL.md`](SKILL.md) — portable discovery/bootstrap entrypoint.
- [`AGENTS.md`](AGENTS.md) — authoritative literary and durable-state rules.
- [`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md) — setup, version pinning, installation, collisions, and capabilities.
- [`docs/ORCHESTRATION.md`](docs/ORCHESTRATION.md) — isolated workers, bounded context, fallbacks, and single-writer state.
- [`docs/TRANSLATION_GUIDE.md`](docs/TRANSLATION_GUIDE.md) — repository state, formats, resume flow, and validation.

## License

MIT. See [`LICENSE`](LICENSE).
