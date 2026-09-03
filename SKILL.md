---
name: book-translator
description: Translate and review full books with a resumable, source-grounded workflow. Designed for ChatGPT Web and Codex, while remaining agent-agnostic for other capable AI agents that can read repository instructions and access the source book.
---

# Book Translator

Use this skill when the user wants to translate, review, resume, or assemble a full book translation.

Canonical implementation: `https://github.com/tim8es/book-translator`

## Contract

This skill is a thin entrypoint, not a second copy of the workflow.

1. Read `agent-manifest.json`.
2. Read `AGENTS.md` and treat it as authoritative.
3. Use `docs/AGENT_SETUP.md` only for environment/bootstrap details.
4. If the user did not pin a version, resolve the latest `main` once and record the resolved commit according to the manifest.
5. Ask only for genuinely missing required inputs: the source book and target language.
6. Follow repository state rather than chat history when resuming.

Do not duplicate or reinterpret the translation-quality rules in this file.

## Primary usage modes

### ChatGPT Web

Use repository-reading, file, connector, or workspace capabilities that are actually available. Do not assume shell, Git, or persistent repository writes. If a required capability is unavailable, identify the exact missing capability and request only the smallest manual step needed.

### Codex

Prefer the full repository workflow: clone or open the repository, run the optional Python helper when appropriate, persist state to files, run tests/validation, and use Git for durable checkpoints when the workspace permits it.

### Other AI agents

The workflow is agent-agnostic. Any capable AI agent may use it if it can:

- read the repository instructions;
- access the source book;
- produce target-language text;
- persist or return the workflow files needed for progress.

Filesystem, shell, Git, GitHub, Python, or artifact-generation access increase automation but are not mandatory for the literary workflow itself.

## Do not

- claim capabilities the active environment does not have;
- publish source books merely because the canonical repository is public;
- create a second divergent copy of the translation methodology inside another skill;
- run multiple writers against the same book state without the concurrency rules in `AGENTS.md`;
- mark a chapter `reviewed` without source-comparison review.
