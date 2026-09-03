---
name: book-translator
description: Translate and review full books with durable repository state, literary fidelity checks, resumable progress, and role-routed agent context. Designed primarily for ChatGPT Web and Codex while remaining agent-agnostic.
---

# Book Translator

Use this skill when the user wants to translate, review, resume, or assemble a full book translation.

Canonical repository: `https://github.com/tim8es/book-translator`

This file is a thin discovery/bootstrap entrypoint. It does not define literary, setup, orchestration, review, or state-transition rules; those live in the repository contracts selected through `agent-manifest.json`.

## Compatibility

Primary documented clients:

- ChatGPT Web;
- Codex.

The workflow is agent-agnostic. Other capable AI agents may use it when they can read the repository contract and access the source book. Never claim a filesystem, Git, shell, repository, worker, file-writing, or artifact capability that the active environment does not actually provide.

## One-link interface

The normal user-facing invocation is intentionally small:

1. repository URL;
2. source book;
3. target language.

Example:

```text
Use https://github.com/tim8es/book-translator to translate this book into Russian.
```

Before asking the user for anything, inspect the available conversation context, attachments, workspace, and source metadata. Ask only when a manifest-required semantic input is genuinely missing or ambiguity would risk operating on the wrong source or deliverable.

## Bootstrap

1. Preserve an explicitly pinned branch, tag, or commit; otherwise use the manifest default ref.
2. Resolve that ref once to a concrete revision when the environment permits it.
3. Read `agent-manifest.json` from that selected ref/revision.
4. Select the `context_profiles` entry matching the current role.
5. Load only the contract files named by that profile, all from the same workflow revision.
6. Do not carry setup or orchestration contracts into `translator` or `reviewer` context unless the agent is explicitly transitioning roles.

For a new or not-yet-installed workspace, begin with the `bootstrap` profile. After setup, transition to `orchestrator`. When a role is already explicit, select the matching profile directly.

Do not silently switch workflow revision during an active book run. A workflow upgrade is a separate explicit transition governed by the appropriate repository contract.

## Repository authority

`agent-manifest.json` is the routing entrypoint. The files referenced by the selected `context_profiles` entry are authoritative for that role.

Do not reconstruct missing rules from README text, chat history, an older revision, or another role's contract when the selected revision is available.
