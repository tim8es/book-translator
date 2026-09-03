# Agent setup protocol

This document defines what an AI agent should do when a user gives it the Book Translator repository URL.

The authoritative literary and state rules are in [`../AGENTS.md`](../AGENTS.md). Execution topology and bounded-context rules are in [`ORCHESTRATION.md`](ORCHESTRATION.md). This file focuses on bootstrap and environment handling.

## Goal

A user should not need to understand this repository before using it.

The normal interaction is:

1. user gives the agent `https://github.com/tim8es/book-translator`;
2. user provides a source book, now or later;
3. user specifies the target language, now or later;
4. the agent sets up everything else it can safely determine itself.

The agent should not push technical configuration or orchestration decisions back to the user merely because asking is easier.

## Bootstrap decision tree

### 1. Read the repository contract

Read, in this order:

1. `agent-manifest.json`;
2. `SKILL.md` when the environment supports skill-style instructions or when bootstrap intent needs clarification;
3. `AGENTS.md`;
4. this document for environment/bootstrap details;
5. `docs/ORCHESTRATION.md` before starting or resuming chapter execution.

### 2. Resolve the workflow version once

If the user pinned a branch, tag, or commit, preserve that choice.

If the user did not specify a version, resolve the latest `main` at setup time.

When the environment can resolve a concrete revision, record it in the writable project as `.book-translator-install.json`:

```json
{
  "schema_version": 1,
  "canonical_repository": "https://github.com/tim8es/book-translator",
  "requested_ref": "main",
  "resolved_revision": "<concrete-commit-sha>"
}
```

Use the actually requested ref instead of `main` when the user pinned one.

Do not silently switch to a newer `main` during an active book run. Updating the workflow is an explicit upgrade step followed by compatibility/validation checks.

If the environment cannot resolve a concrete revision, preserve the most specific ref/source information it has and state that exact reproducibility is unavailable there. Do not invent a commit SHA.

### 3. Detect available capabilities

Determine whether you can:

- access the repository;
- write files;
- use Git;
- run Python;
- read the supplied source format;
- create isolated worker sessions/subagents;
- create the final requested artifact format.

Do not ask the user to enumerate your capabilities. Inspect the environment yourself.

Choose execution mode according to `agent-manifest.json` and `docs/ORCHESTRATION.md`:

- isolated workers available -> `isolated_workers`;
- isolated workers unavailable -> `single_agent_bounded_context`.

The fallback is automatic. Do not ask the user to orchestrate subagents manually.

### 4. Obtain the template

#### Filesystem + Git available

Prefer cloning the resolved ref/revision:

```bash
git clone https://github.com/tim8es/book-translator.git
cd book-translator
git checkout <resolved-revision>
```

A shallow clone is acceptable only when it still lets the agent resolve and preserve the selected revision correctly.

If already inside a user repository and the user explicitly wants the workflow integrated there, copy the canonical workflow files deliberately instead of nesting an unrelated Git repository. Never copy user/example book contents from the canonical `books/` directory.

#### Repository API available, shell unavailable

Read files from the resolved canonical revision and create the equivalent workflow files in the writable target workspace.

For a normal runtime installation, copy this deterministic set:

- `SKILL.md`;
- `AGENTS.md`;
- `agent-manifest.json`;
- `docs/AGENT_SETUP.md`;
- `docs/ORCHESTRATION.md`;
- `docs/TRANSLATION_GUIDE.md`;
- `docs/templates/`;
- `scripts/book.py`;
- `books/.gitkeep` for an empty book root.

For a development copy, additionally copy:

- `tests/`;
- `.github/`;
- `.gitignore`;
- `LICENSE`;
- `README.md`.

Do not use vague rules such as "copy scripts when useful". Select either the runtime installation set or the development-copy set from the user's actual intent.

Do not copy any real book workspace from the canonical repository.

#### Read-only environment

Read the repository and explain the smallest manual step that blocks execution.

Examples:

- upload the source book;
- enable file/repository write access;
- clone the repository locally;
- provide a writable working directory.

Do not claim files were created, commands were run, subagents were launched, or setup completed when they were not.

## Required user inputs

Only these are mandatory for a real translation:

1. **source book**;
2. **target language**.

Before asking, inspect the conversation, attachments, workspace, and source metadata.

### Do not ask when you can infer safely

Do not ask the user for:

- a book slug — derive one;
- output directory — use the repository convention;
- source format — detect it;
- title/author — read them when available;
- source language — detect it when reliable;
- chapter filenames — derive stable names;
- whether to create glossary/style-guide files — always create them;
- whether to validate state — always validate;
- which execution mode to use — detect capabilities and choose it;
- whether to create Translator/Reviewer workers — follow the orchestration contract;
- which chapter to resume when exactly one incomplete book is active — use `progress.json`.

### Ask when guessing would change the task

Ask for the target language if it is genuinely absent.

Ask for the source book if it is not accessible.

If several incomplete book workspaces exist and the conversation does not identify which one to resume, ask only which book to use. Do not select one arbitrarily.

Ask about other ambiguous user intent only when proceeding would risk translating the wrong material, overwriting existing work, or producing the wrong deliverable.

## Book initialization

When Python is available and the format is supported by the helper, prefer:

```bash
python scripts/book.py extract /path/to/source.epub --target-language ru
```

Optional flags:

```text
--slug
--title
--author
--source-language
```

Do not request optional values from the user when they can be derived.

After extraction:

1. verify the immutable source copy exists;
2. inspect chapter order and boundaries;
3. run structural validation;
4. populate an initial evidence-based `style-guide.md` from the source;
5. populate glossary entries only when recurring decisions exist;
6. load `docs/ORCHESTRATION.md`;
7. begin the sequential chapter workflow.

If the helper cannot be run, reproduce the same repository state directly according to `AGENTS.md` and `docs/TRANSLATION_GUIDE.md`.

## Supported format behavior

### EPUB

Use the EPUB package/spine reading order. Do not assume every XHTML file is a chapter.

### HTML / XHTML / Markdown / TXT

Use explicit document structure. When chapter boundaries are ambiguous, preserve a larger unit instead of inventing chapters.

### DOCX / PDF

The repository structure still applies, but the included helper does not claim automatic extraction.

If the active agent can reliably read the file, it may initialize the workspace itself. Otherwise state the specific extraction limitation and request only the minimal manual conversion/extraction step.

## Translation loop

Use the orchestration protocol instead of carrying the whole book in one growing agent context.

Default sequence:

1. orchestrator selects the next unreviewed chapter;
2. fresh Translator worker (or bounded Translator role) receives the current source plus required canonical context;
3. translation artifact is created and checked for existence/completeness;
4. fresh independent Reviewer worker (or bounded Reviewer role) compares source and translation;
5. corrections are applied;
6. orchestrator accepts any glossary/style proposals;
7. orchestrator updates `progress.json` and global state;
8. structural validation runs;
9. only then proceed to the next chapter.

Do not translate multiple chapters concurrently by default.

## Resuming

Repository state is more authoritative than chat history.

First determine the active book deterministically:

1. if the user names/selects a book, use it;
2. otherwise enumerate valid book workspaces;
3. if exactly one incomplete book exists, use it;
4. if multiple incomplete books exist, ask which one to resume;
5. never choose arbitrarily.

Then read:

1. `.book-translator-install.json` when present;
2. `AGENTS.md`;
3. `docs/ORCHESTRATION.md`;
4. the book's `metadata.json`;
5. `progress.json`;
6. `glossary.md`;
7. `style-guide.md`;
8. only the bounded existing translation/source context needed for continuity.

Continue from the first chapter that is not `reviewed`, unless the user explicitly requests another chapter or a quality re-review.

Do not retranslate reviewed chapters without a concrete reason.

## Validation

When Python is available:

```bash
python scripts/book.py validate <book-slug>
```

The agent must still inspect literary quality itself. Structural validation cannot prove translation fidelity.

If validation fails, do not start the next chapter until state is repaired and validation passes.

## Building output

Markdown is the safe default:

```bash
python scripts/book.py build <book-slug>
```

Only claim EPUB, DOCX, or PDF delivery if the active environment actually created and checked that artifact.

## Privacy and repository hygiene

- Never modify the source file stored under `source/`.
- Never publish a user's copyrighted book merely because the workflow repository is public.
- Never copy private/example books from the canonical template into a new installation.
- Never commit secrets or API keys.
- The default workflow needs no LLM API key.

## Definition of successful setup

Setup is complete when:

- the workflow files are available in a writable workspace, or the exact read-only limitation is stated;
- the selected workflow ref/revision is resolved as specifically as the environment permits;
- the resolved revision is recorded when possible;
- `AGENTS.md` is loaded as the authoritative literary/state instruction;
- the execution mode is selected from actual capabilities;
- the source book is accessible or explicitly identified as the only missing input;
- the target language is known or explicitly identified as the only missing semantic input;
- the agent can state the next executable step without requiring the user to understand repository internals or orchestration.
