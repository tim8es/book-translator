# Book Translator

**Agent-native workflow for translating full books with persistent state, literary fidelity checks, and resumable review.**

This repository is designed to be handed directly to an AI coding agent. The agent should read the repository instructions, set up everything it can by itself, ask only for genuinely missing user inputs, and then manage the translation as a durable file-based project.

> Repository URL: `https://github.com/tim8es/book-translator`

## Fastest start

Give your AI agent:

1. this repository URL;
2. the source book file;
3. the target language.

That is enough for the normal workflow.

If you provide only the repository URL first, the agent should inspect [`agent-manifest.json`](agent-manifest.json) and [`AGENTS.md`](AGENTS.md), then ask only for required inputs it cannot infer or access.

### Example

```text
Use https://github.com/tim8es/book-translator to translate this book into Russian.
Set up everything you can automatically, follow the repository instructions,
and continue until the translation is reviewed.
```

You do **not** need to explain the repository structure, copy prompts manually, install an LLM SDK, create a database, or configure an API key for the default agent-driven workflow.

## What the agent should do automatically

When given this repository, a capable agent should:

1. read `agent-manifest.json` and `AGENTS.md`;
2. use the latest `main` unless you explicitly request a tag/commit/ref;
3. clone or otherwise obtain the repository when it has filesystem access;
4. inspect its own capabilities and use the documented fallback when a capability is unavailable;
5. identify the source book and target language from the conversation/files;
6. ask only for required information that is actually missing;
7. create a per-book workspace under `books/<book-slug>/`;
8. preserve the original source unchanged;
9. extract chapters in real reading order;
10. initialize translation metadata, progress, glossary, and style guide;
11. translate chapter by chapter;
12. perform a separate source-to-translation fidelity review before marking a chapter `reviewed`;
13. persist progress so another agent/session can resume without chat history;
14. validate repository state and build the requested output.

The authoritative behavior and quality rules are in [`AGENTS.md`](AGENTS.md).

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
├── AGENTS.md                 # authoritative agent + translation rules
├── agent-manifest.json       # machine-readable bootstrap contract
├── books/                    # user book workspaces; template is empty
├── docs/
│   ├── AGENT_SETUP.md        # capability-aware setup/bootstrap behavior
│   ├── TRANSLATION_GUIDE.md  # repository state and workflow reference
│   └── templates/            # per-book state templates
├── scripts/
│   └── book.py               # optional stdlib-only extraction/validation/build helper
└── tests/
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

A new session should read repository state and continue from the first chapter that is not `reviewed`. It should not depend on prior chat messages when the repository contains the needed state.

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

- **Agent-first:** a repository URL is enough to discover the workflow.
- **File-based:** state stays transparent and inspectable.
- **Resumable:** another session can continue from repository state.
- **Fidelity-first:** the translator is an interpreter, not a co-author.
- **Capability-aware:** automate what the agent can do; explain only the unavoidable manual step.
- **No hidden infrastructure:** no database, backend, queue, or mandatory LLM API integration.

## Documentation

- [`AGENTS.md`](AGENTS.md) — authoritative execution and literary-quality rules.
- [`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md) — what an agent should do when it receives only the repository URL.
- [`docs/TRANSLATION_GUIDE.md`](docs/TRANSLATION_GUIDE.md) — repository state, formats, resume flow, and validation.

## License

MIT. See [`LICENSE`](LICENSE).
