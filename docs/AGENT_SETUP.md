# Agent setup protocol

This document defines what an AI agent should do when a user gives it the Book Translator repository URL.

Literary and durable-state rules are in [`../AGENTS.md`](../AGENTS.md). Execution topology and bounded-context rules are in [`ORCHESTRATION.md`](ORCHESTRATION.md). This file controls bootstrap, version resolution, installation layout, capability handling, and resume setup.

## Canonical contract order

`agent-manifest.json` is the machine-readable bootstrap contract. Its `contract_read_order` is the single canonical order for loading Book Translator instructions.

Do not maintain or invent a competing read order in another document. Every contract file used for one run must come from the same resolved workflow revision.

The normal user-facing interaction remains:

1. repository URL;
2. source book;
3. target language.

The agent should determine technical defaults and orchestration choices itself.

## Bootstrap decision tree

### 1. Resolve the requested workflow ref

First derive the requested ref from user intent:

- if the user pinned a branch, tag, commit, or branch URL, preserve it exactly as the requested ref;
- otherwise use the manifest default, currently `main`.

When possible, resolve that requested ref once to a concrete commit SHA before doing writable work.

Do not silently move to a newer `main` or another ref during an active book run.

### 2. Read one coherent contract revision

Read the files listed by `agent-manifest.json.contract_read_order` from that same resolved revision.

If the environment cannot resolve a concrete revision, use the most specific requested ref available, keep all contract files on that same ref, and state that exact revision reproducibility is unavailable. Never invent a SHA.

### 3. Detect available capabilities

Determine whether you can:

- access the repository and the selected ref/revision;
- write files;
- use Git;
- run Python;
- read the supplied source format;
- create isolated worker sessions/subagents;
- create the requested final artifact format.

Do not ask the user to enumerate your capabilities.

Choose execution mode automatically:

- isolated workers available -> `isolated_workers`;
- otherwise -> `single_agent_bounded_context`.

### 4. Obtain a writable installation

#### Standalone filesystem + Git

For a normal clone, resolve the requested ref to a concrete revision and then check out that exact revision:

```bash
git clone https://github.com/tim8es/book-translator.git
cd book-translator
git checkout --detach <resolved-revision>
```

Do not use a default-branch-only shallow clone when it would prevent the requested branch, tag, or commit from being resolved and checked out.

For a standalone Book Translator repository, `install_root` is `.`.

#### Repository API without shell

Read every runtime file from the same resolved revision and create the equivalent runtime installation in the writable target workspace. Never mix files fetched from different refs or revisions.

#### Existing repository collision policy

When the user asks to integrate Book Translator into an existing repository, first inspect all runtime target paths.

**Never overwrite a pre-existing unrelated file.** Use this deterministic policy:

1. If all runtime target paths are absent, or already contain the same Book Translator-managed files, use `install_root = "."`.
2. If any runtime path conflicts with an unrelated host file, do not replace or merge that file automatically.
3. Instead, install the **entire** Book Translator runtime set under `install_root = ".book-translator"`.
4. Never split one installation between the host root and `.book-translator/`.
5. If `.book-translator/` itself contains an unrelated conflicting installation and safe ownership cannot be established, stop before writing and ask only for the minimum choice needed to avoid overwriting user data.

For a namespaced installation, paths are relative to `.book-translator/`, for example:

```text
.book-translator/AGENTS.md
.book-translator/docs/ORCHESTRATION.md
.book-translator/scripts/book.py
.book-translator/books/<book-slug>/
```

Run helper commands relative to the selected installation root, for example:

```bash
python .book-translator/scripts/book.py validate <book-slug>
```

when `install_root` is `.book-translator`.

### 5. Copy the deterministic runtime set

Relative to `install_root`, a normal runtime installation contains:

- `SKILL.md`;
- `AGENTS.md`;
- `agent-manifest.json`;
- `docs/AGENT_SETUP.md`;
- `docs/ORCHESTRATION.md`;
- `docs/TRANSLATION_GUIDE.md`;
- `docs/templates/`;
- `scripts/book.py`;
- `books/.gitkeep` for an empty book root.

A development copy additionally contains:

- `tests/`;
- `.github/`;
- `.gitignore`;
- `LICENSE`;
- `README.md`.

Never copy a real book workspace from the canonical repository.

### 6. Record installation provenance

After `install_root` and the resolved workflow revision are known, write `<install_root>/.book-translator-install.json`:

```json
{
  "schema_version": 1,
  "canonical_repository": "https://github.com/tim8es/book-translator",
  "requested_ref": "main",
  "resolved_revision": "<concrete-commit-sha>",
  "install_root": "."
}
```

Use the real requested ref and the actual `install_root`. If a concrete revision cannot be resolved, use `null` for `resolved_revision`; never fabricate one.

This file describes the currently installed workflow. It is **not** the only provenance for books that may have been created under older revisions.

#### Read-only environment

If no writable installation is possible, identify the exact unavailable operation and the smallest manual step required. Do not claim setup, file writes, commands, workers, or artifacts were created when they were not.

## Required user inputs

Only these are mandatory for a real translation:

1. **source book**;
2. **target language**.

Before asking, inspect the conversation, attachments, workspace, and source metadata.

Do not ask for values that can be derived safely, including book slug, directories, source format, title/author when available, source language when reliably detectable, chapter filenames, glossary/style-guide creation, validation, execution mode, or worker topology.

If several incomplete books exist and user intent does not identify one, ask only which book to resume. Do not choose arbitrarily.

## Book initialization

When Python is available and the format is supported by the helper, prefer:

```bash
python scripts/book.py extract /path/to/source.epub --target-language ru
```

Use the equivalent path under `.book-translator/` for a namespaced installation.

After extraction:

1. verify the immutable source copy exists;
2. inspect chapter order and boundaries;
3. run structural validation;
4. populate an initial evidence-based `style-guide.md`;
5. add glossary entries only when recurring decisions exist;
6. verify `metadata.json.workflow` records the workflow repository, requested ref, and resolved_revision used to initialize that book;
7. load `docs/ORCHESTRATION.md`;
8. begin the sequential chapter workflow.

If the helper cannot be run, reproduce the same repository state directly according to `AGENTS.md` and `docs/TRANSLATION_GUIDE.md`, including per-book workflow provenance.

## Supported format behavior

### EPUB

Use the EPUB package/spine reading order. Do not assume every XHTML file is a chapter.

### HTML / XHTML / Markdown / TXT

Use explicit document structure. When chapter boundaries are ambiguous, preserve a larger unit instead of inventing chapters.

### DOCX / PDF

The repository structure still applies, but the included helper does not claim automatic extraction. If the active agent cannot reliably read the file, request only the minimum conversion/extraction step required.

## Translation loop

Follow `docs/ORCHESTRATION.md` rather than carrying the whole book in one growing context.

Default sequence:

1. orchestrator selects the next unreviewed chapter;
2. Translator worker/role receives bounded canonical context;
3. translation artifact is created and checked;
4. independent Reviewer worker/role compares source and translation;
5. corrections are applied;
6. orchestrator accepts global glossary/style decisions;
7. orchestrator updates `progress.json` and global state;
8. structural validation runs;
9. only then proceed to the next chapter.

Do not translate multiple chapters concurrently by default.

## Resuming

Repository state is more authoritative than chat history.

First identify the installation root and active book deterministically. Then:

1. read `agent-manifest.json.contract_read_order` for the workflow revision associated with that book;
2. read `<install_root>/.book-translator-install.json` when present;
3. read the book's `metadata.json.workflow`;
4. treat the book-level `workflow.resolved_revision` as the provenance of that book;
5. if the installed workflow revision differs, do **not** silently upgrade the book — load the recorded book revision for its contract, or perform an explicit documented upgrade before continuing;
6. read `progress.json`, `glossary.md`, `style-guide.md`, and only bounded continuity context;
7. continue from the first chapter that is not `reviewed`, unless the user explicitly requests another scope.

This allows different books in one workspace to retain the workflow revision under which each was initialized.

## Validation

When Python is available:

```bash
python scripts/book.py validate <book-slug>
```

Structural validation cannot prove literary fidelity. If validation fails, repair state before starting the next chapter.

## Building output

Markdown is the safe default:

```bash
python scripts/book.py build <book-slug>
```

Only claim EPUB, DOCX, or PDF delivery if the active environment actually created and checked that artifact.

## Privacy and repository hygiene

- Never modify the source file stored under `source/`.
- Never publish a user's copyrighted book merely because the workflow repository is public or later becomes public.
- Never copy private/example books from the canonical template into a new installation.
- Never commit secrets or API keys.
- The default workflow needs no LLM API key.

## Definition of successful setup

Setup is complete when:

- one coherent workflow revision has been selected;
- the files in `contract_read_order` come from that revision;
- a writable `install_root` is selected without overwriting unrelated host files, or the exact read-only limitation is stated;
- installation provenance is recorded as specifically as the environment permits;
- execution mode is selected from actual capabilities;
- the source book and target language are available or are the only explicitly identified missing required inputs;
- the next executable step is known without requiring the user to understand repository internals.
