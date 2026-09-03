# Agent setup protocol

This file is authoritative for workflow revision resolution, capability detection, installation layout, collision handling, and installation provenance.

It is loaded by the `bootstrap` context profile together with `AGENTS.md`. It does not define literary translation, review criteria, chapter sequencing, or durable chapter-state transitions.

After setup, transition to the `orchestrator` context profile from `agent-manifest.json`. Setup policy is no longer part of Translator or Reviewer context.

## Resolve the workflow revision

Start from the repository/ref supplied by the user:

- preserve an explicitly pinned branch, tag, commit, or branch URL;
- otherwise use `version_policy.default_ref` from `agent-manifest.json`.

When possible, resolve the requested ref once to a concrete commit SHA before doing writable work.

Use one coherent workflow revision for all contract and runtime files selected during setup. Do not silently move to another ref or a newer `main` during the active run. If a concrete revision cannot be resolved, keep the most specific requested ref available, record `null` for the unresolved SHA, and do not claim exact reproducibility.

## Detect capabilities

Determine from the active environment whether you can:

- access the selected repository ref/revision;
- write files;
- use Git;
- run Python;
- read the supplied source format;
- create isolated workers or subagents;
- create the requested final artifact format.

Do not ask the user to enumerate capabilities the environment can reveal directly.

Capability detection is setup information. Execution topology is chosen later under `docs/ORCHESTRATION.md`.

## Obtain a writable installation

### Standalone filesystem + Git

For a standalone checkout, resolve the requested ref and then check out that exact revision:

```bash
git clone https://github.com/tim8es/book-translator.git
cd book-translator
git checkout --detach <resolved-revision>
```

Do not use a default-branch-only shallow clone strategy when it would prevent an explicitly requested branch, tag, or commit from being resolved and checked out.

For a standalone Book Translator repository, `install_root` is `.`.

### Repository API without shell

Read every required runtime file from the same resolved revision and create the equivalent runtime installation in the writable target workspace.

Never mix files fetched from different workflow refs or revisions.

### Existing repository collision policy

When Book Translator is integrated into an existing repository, inspect all runtime target paths before writing.

**Never overwrite a pre-existing unrelated file.** Use this deterministic policy:

1. If all runtime target paths are absent, or already contain the same Book Translator-managed files, use `install_root = "."`.
2. If any runtime path conflicts with an unrelated host file, do not replace, merge, or rewrite that file automatically.
3. Install the **entire** Book Translator runtime set under `install_root = ".book-translator"`.
4. Never split one Book Translator installation between the host root and `.book-translator/`.
5. If `.book-translator/` itself contains unrelated conflicting content and ownership cannot safely be established, stop before writing and ask only for the minimum choice required to avoid overwriting user data.

For a namespaced installation, examples include:

```text
.book-translator/AGENTS.md
.book-translator/docs/ORCHESTRATION.md
.book-translator/docs/TRANSLATION.md
.book-translator/scripts/book.py
.book-translator/books/<book-slug>/
```

Helper commands are run relative to the chosen installation root, for example:

```bash
python .book-translator/scripts/book.py validate <book-slug>
```

when `install_root` is `.book-translator`.

## Runtime installation set

Relative to `install_root`, a normal runtime installation contains:

- `SKILL.md`;
- `AGENTS.md`;
- `agent-manifest.json`;
- `docs/AGENT_SETUP.md`;
- `docs/ORCHESTRATION.md`;
- `docs/TRANSLATION.md`;
- `docs/templates/`;
- `scripts/book.py`;
- `books/.gitkeep` for an empty book root.

A development copy additionally contains repository-development files such as:

- `tests/`;
- `.github/`;
- `.gitignore`;
- `LICENSE`;
- `README.md`;
- design/implementation planning documents.

Do not copy a real book workspace from the canonical repository into a new installation.

## Record installation provenance

After `install_root` and the workflow revision are known, write `<install_root>/.book-translator-install.json`:

```json
{
  "schema_version": 1,
  "canonical_repository": "https://github.com/tim8es/book-translator",
  "requested_ref": "main",
  "resolved_revision": "<concrete-commit-sha>",
  "install_root": "."
}
```

Use the actual requested ref and actual installation root. If a concrete revision cannot be resolved, use `null` for `resolved_revision`; never fabricate a SHA.

The installation record describes the workflow currently installed at that root. It does not override provenance already recorded for an existing book.

## Required semantic inputs

The required semantic inputs are declared by `agent-manifest.json.required_user_inputs`.

Before asking the user for a missing input, inspect the conversation, attachments, workspace, filename, and source metadata. Do not ask for technical defaults that can be derived safely.

If the required inputs are already available, setup should continue without an extra confirmation step.

## Helper and source-format capability

`scripts/book.py` is optional and uses only the Python standard library.

The source formats it can extract automatically are declared in `agent-manifest.json.source_formats.automatic_helper`. Formats declared in `agent_dependent` require reliable format handling by the active agent or an explicit minimal conversion/extraction step.

For EPUB extraction, preserve the package/spine reading order; do not assume every XHTML resource is a chapter.

For HTML, XHTML, Markdown, and text sources, follow explicit document structure. If chapter boundaries are ambiguous, preserve a larger unit rather than inventing chapters.

For DOCX or PDF, do not claim automatic helper support that does not exist. Continue only when the active environment can read/extract the source reliably or the minimum required conversion has been provided.

## Read-only fallback

If no writable installation is possible, identify the exact unavailable operation and the smallest manual step required.

Do not claim setup, file writes, Git operations, commands, workers, or artifacts were created when they were not.

## Setup handoff

Setup is complete when:

- one coherent workflow ref/revision has been selected;
- the `bootstrap` contracts and installed runtime files come from that same revision;
- a writable `install_root` was selected without overwriting unrelated host files, or the exact read-only limitation was stated;
- installation provenance was recorded as specifically as the environment permits;
- relevant capabilities, including Python/source-format support and isolated workers, were detected;
- manifest-required semantic inputs are available or are the only identified missing inputs.

Then transition to the `orchestrator` context profile. Do not carry this setup document into Translator or Reviewer context merely because it was read during bootstrap.
