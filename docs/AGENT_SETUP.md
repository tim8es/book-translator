# Agent setup protocol

This document defines what an AI agent should do when a user gives it the Book Translator repository URL.

The authoritative execution and translation-quality rules are in [`../AGENTS.md`](../AGENTS.md). This file focuses on bootstrap and environment handling.

## Goal

A user should not need to understand this repository before using it.

The normal interaction is:

1. user gives the agent `https://github.com/tim8es/book-translator`;
2. user provides a source book, now or later;
3. user specifies the target language, now or later;
4. the agent sets up everything else it can safely determine itself.

The agent should not push technical configuration decisions back to the user merely because asking is easier.

## Bootstrap decision tree

### 1. Read the repository contract

Read, in this order:

1. `agent-manifest.json`;
2. `AGENTS.md`;
3. this document only when bootstrap details are needed.

If the user did not specify a version, use the latest `main`.

If the user pinned a branch, tag, or commit, preserve that choice.

### 2. Detect available capabilities

Determine whether you can:

- access the repository;
- write files;
- use Git;
- run Python;
- read the supplied source format;
- create the final requested artifact format.

Do not ask the user to enumerate your capabilities. Inspect the environment yourself.

### 3. Obtain the template

#### Filesystem + Git available

Prefer:

```bash
git clone --depth 1 https://github.com/tim8es/book-translator.git
cd book-translator
```

If already inside a user repository and the user explicitly wants the workflow integrated there, copy the canonical workflow files deliberately instead of nesting an unrelated Git repository. Never copy user/example book contents from the canonical `books/` directory.

#### Repository API available, shell unavailable

Read the canonical files through the repository API and create the equivalent workflow files in the writable target workspace.

The canonical files are:

- `AGENTS.md`;
- `agent-manifest.json`;
- `docs/`;
- `scripts/` when useful;
- `tests/` when the target is a development copy;
- `books/.gitkeep` for an empty book root.

Do not copy any real book workspace from the canonical repository.

#### Read-only environment

Read the repository and explain the smallest manual step that blocks execution.

Examples:

- upload the source book;
- enable file/repository write access;
- clone the repository locally;
- provide a writable working directory.

Do not claim files were created, commands were run, or setup completed when they were not.

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
- which chapter to resume — use `progress.json` unless explicitly overridden.

### Ask when guessing would change the task

Ask for the target language if it is genuinely absent.

Ask for the source book if it is not accessible.

Ask about ambiguous user intent only when proceeding would risk translating the wrong material, overwriting existing work, or producing the wrong deliverable.

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
6. begin translation.

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

For each chapter:

1. read relevant source context;
2. read glossary and style-guide decisions;
3. create a complete translation draft;
4. mark the chapter `translated` only after the translation file exists;
5. perform a separate sequential comparison against the original;
6. correct omissions, additions, semantic drift, changed tone, lost ambiguity, voice drift, and formatting errors;
7. polish target-language prose without rewriting the author;
8. re-check substantial polish changes against the source;
9. mark `reviewed` only after the required source-comparison review actually occurred;
10. persist `progress.json` in the same session.

Continue with the next unreviewed chapter unless the user explicitly changes scope.

## Resuming

Repository state is more authoritative than chat history.

Read:

1. `AGENTS.md`;
2. `metadata.json`;
3. `progress.json`;
4. `glossary.md`;
5. `style-guide.md`;
6. existing translations needed for continuity.

Continue from the first chapter that is not `reviewed`.

Do not retranslate reviewed chapters without a concrete reason.

## Validation

When Python is available:

```bash
python scripts/book.py validate <book-slug>
```

The agent must still inspect literary quality itself. Structural validation cannot prove translation fidelity.

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

- the workflow files are available in a writable workspace;
- `AGENTS.md` is loaded as the authoritative instruction;
- the source book is accessible or the agent has explicitly identified that as the only missing input;
- the target language is known or explicitly identified as the only missing semantic input;
- the agent can state the next executable step without requiring the user to understand repository internals.
