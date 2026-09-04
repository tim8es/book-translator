# AGENTS.md

## Purpose

This file contains the small set of global invariants that apply to every Book Translator role. It is intentionally safe to auto-load.

Role-specific rules are selected through `agent-manifest.json.context_profiles`. Do not load every contract by default.

## Role routing

1. Read `agent-manifest.json` from the workflow revision selected for the current run or book.
2. Identify the current role: `bootstrap`, `orchestrator`, `translator`, or `reviewer`.
3. Load only the contract files named by that role's `context_profiles` entry from the same workflow revision.
4. Load another role's contract only when explicitly transitioning into that role or resolving a dependency documented by the active contract.

README is descriptive documentation, not execution policy.

## Durable state over chat history

When durable repository state is available, treat it as more authoritative than conversational memory.

Do not require prior chat history to resume work that can be reconstructed from the repository. Do not overwrite durable state with an assumption remembered from conversation when the files say otherwise.

## Workflow revision stability

Preserve an explicitly selected workflow revision for the active book run.

Do not silently move to a newer `main`, another branch, another tag, or another commit while operating on an existing book. Book-level workflow provenance controls resume behavior when it exists. An upgrade is an explicit transition governed by the setup/orchestration contracts.

If a concrete revision cannot be resolved, do not fabricate one and do not claim exact reproducibility.

## Source immutability

Treat source material as immutable input.

Never overwrite, rewrite, normalize in place, or replace the user's preserved original source as part of translation work. Derived extraction and translation artifacts must remain separate from the preserved source.

## Capability honesty

Use only capabilities that are actually available in the active environment.

Do not claim that files, commands, workers, Git operations, repository writes, reviews, validations, or deliverable artifacts were created or executed when they were not.

When a required operation is unavailable, state the exact limitation and use the documented fallback for the active role. Ask the user for a manual step only when no safe documented fallback exists.

## Shared-state ownership

Shared mutable book state is orchestrator-owned.

A `translator` or `reviewer` role may return artifacts, findings, warnings, and proposed glossary/style decisions, but must not independently race to persist shared state. State-transition details belong to `docs/ORCHESTRATION.md` and are loaded only by the `orchestrator` profile.

## User interaction

The workflow should require as little technical coordination from the user as possible.

Before asking a question, inspect the available conversation, attachments, source metadata, repository state, and manifest defaults. Ask only for a genuinely missing semantic input or a choice required to avoid destructive, ambiguous, or materially different work.

Do not ask the user to choose internal worker topology, directories, slugs, helper usage, or other safe technical defaults that the active contracts can determine.

## Privacy, copyright, and repository hygiene

- The canonical workflow repository is not a book-distribution repository.
- Do not publish copyrighted source books or private translations merely because a repository or tool can publish them.
- Prefer private working state when source material or translations should remain private.
- Never copy another user's or example book contents into a new workspace.
- Never commit credentials, API keys, tokens, or unrelated secrets.

## Scope discipline

Keep Book Translator file-based, inspectable, and resumable.

Do not introduce a database, backend, queue, web UI, mandatory LLM SDK, mandatory API key, or external orchestration service unless a later task explicitly requires it.

Preserve clear role boundaries instead of compensating for ambiguity by loading more context.
