# Zero-User Onboarding Design

## Goal

Make Book Translator understandable and usable by a person with no Git, Python, repository, or agent-orchestration knowledge, while preserving the role-routed execution contract and avoiding claims that depend on unavailable platform capabilities.

## Product model

README is the human onboarding surface. It is descriptive and remains outside the agent execution contract.

The user should understand three ways to use Book Translator:

1. **Web AI / no installation** — fastest trial path. Give the repository link, source book, and target language to a capable web AI. Appropriate when the work can reasonably finish in the current cloud workspace/session. Do not promise persistence unless the environment actually provides it.
2. **Private GitHub workspace** — recommended default for full-book and multi-session work. Persistent repository state allows later sessions or another capable agent to resume without relying on chat history.
3. **Local workspace** — recommended when the user wants local control/privacy or uses Codex/another coding agent with filesystem access.

Do not use chapter count as the routing criterion. Book/chapter length and available context vary too much. The useful distinction is ephemeral single-session work versus persistent multi-session work.

## Multi-book model

One Book Translator installation/workspace may contain many books under `books/<book-slug>/`. Each book owns its own source, extraction, translation, metadata, progress, glossary, style guide, source-integrity state, and output.

A Git branch is not the primary storage unit for a book. Book branches may be used as an optional advanced isolation mechanism for concurrent work, but beginner onboarding must not require branch management. Persistent book state lives in the book workspace.

For stronger isolation, a user may choose a separate private repository for a sensitive book.

## README information architecture

The README should be ordered for a nontechnical reader:

1. Plain-language product promise.
2. `Start in 30 seconds` with a copy/paste prompt.
3. `Choose how to use it` comparison for Web AI, Private GitHub, and Local.
4. Detailed Option 1: no-install web use, including persistence caveat.
5. Detailed Option 2: private GitHub workspace, marked recommended for full books; agent-managed path first, manual fallback second.
6. Detailed Option 3: local workspace; agent-managed path first, copy/paste Git commands second.
7. `How your books are stored` and multi-book example.
8. `How resuming works` with a day-one/day-two example.
9. `What Book Translator does for you` in user-facing language.
10. Privacy/copyright warning before advanced technical material.
11. FAQ for Python, API keys, Git, multiple books, resume, other agents, PDF/DOCX, privacy.
12. `How it works internally` containing role routing, authority documents, CLI, structure, tests.

## Setup contract clarification

`docs/AGENT_SETUP.md` remains technical and authoritative. Add only the minimum durable-workspace rules needed to support README claims:

- distinguish ephemeral/read-only/transient workspace use from persistent workspace use;
- for a full/multi-session translation, prefer a persistent writable workspace when available;
- one installation can host many `books/<book-slug>/` workspaces;
- do not create one permanent branch per book as a required storage model;
- branch isolation is optional and environment-specific;
- never promise GitHub repository creation or cloud persistence unless the active agent has that capability.

These rules belong to setup because they affect installation/workspace selection, not literary or orchestration behavior.

## Non-goals

- Do not add a mandatory dependency or new runtime service.
- Do not change the literary quality contract.
- Do not change chapter-state semantics.
- Do not change CLI behavior.
- Do not require users to understand context profiles, branches, Git, Python, or subagents before starting.
- Do not claim ChatGPT Web or another web interface always provides a persistent VM/workspace.

## Success criteria

A first-time nontechnical user can answer, from the first half of README:

- What is Book Translator?
- What do I send to my AI agent?
- Which usage mode should I choose?
- Which mode is recommended for a full book?
- Can I continue tomorrow or in another chat?
- Can one workspace contain multiple books?
- Do I need Git, Python, or an API key?
- Should copyrighted/private books be stored privately?

The technical architecture remains available but no longer blocks onboarding.