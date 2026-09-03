---
name: book-translator
description: Translate and review full books with durable repository state, literary fidelity checks, resumable progress, and capability-aware orchestration. Designed primarily for ChatGPT Web and Codex, while remaining agent-agnostic for other capable AI agents.
---

# Book Translator

Use this skill when the user wants to translate, review, resume, or assemble a full book translation.

Canonical repository: `https://github.com/tim8es/book-translator`

This skill is a thin discovery/bootstrap layer. The repository remains the source of truth; do not maintain a second divergent copy of the implementation here.

## Compatibility

Primary documented clients:

- ChatGPT Web;
- Codex.

The workflow is agent-agnostic. Other capable AI agents may use it when they can read repository instructions and access the source book. Filesystem, Git, shell, repository API, file-writing, or isolated-worker capabilities increase automation but are not required to understand the workflow.

Never promise a capability the active environment does not have.

## One-link interface

The normal user-facing invocation stays intentionally small:

1. repository URL;
2. source book;
3. target language.

Example:

```text
Use https://github.com/tim8es/book-translator to translate this book into Russian.
```

Do not require the user to describe the orchestration, repository layout, chapter state machine, worker roles, Python helper, glossary, style guide, or review protocol.

If the source book or target language is genuinely missing, ask only for the missing required input after inspecting available conversation context, attachments, and workspace state.

## Bootstrap contract

If the client loads this skill before repository access, use it only to discover and enter the canonical repository contract.

Once the repository is accessible:

1. preserve an explicitly pinned branch/tag/commit, otherwise use the manifest default ref;
2. resolve that ref once to a concrete revision when possible;
3. read `agent-manifest.json` from that selected ref/revision;
4. follow `agent-manifest.json.contract_read_order` from that same revision;
5. record installation provenance according to `docs/AGENT_SETUP.md`;
6. initialize or resume the book workspace;
7. execute until the requested scope is reviewed and validated.

Do not maintain a second read-order list here. `contract_read_order` is the canonical instruction-loading order.

Do not silently switch workflow revision during an active book run. A later upgrade is a separate explicit state transition.

## Execution mode

Prefer `isolated_workers` when the environment can start independent worker sessions or subagents.

For each chapter, the orchestrator should use:

1. a fresh Translator worker;
2. a separate fresh Reviewer worker;
3. orchestrator validation and global-state commit;
4. then the next chapter.

Use `single_agent_bounded_context` when isolated workers are unavailable. Preserve the same role boundaries and reload only the bounded inputs required for each stage rather than carrying the whole book conversation forward.

Do not parallelize chapter translation by default. Literary continuity is more important than throughput. Advance sequentially after the previous chapter's review and global-state decisions are committed.

## State ownership

The orchestrator owns global mutable state.

Workers may propose glossary/style decisions, warnings, and corrections, but must not independently race to update shared state such as `progress.json`, `glossary.md`, or `style-guide.md`.

The orchestrator applies accepted changes after review.

## Worker context

Give a worker only the context required for its job:

- authoritative repository rules;
- book metadata, including `metadata.json.workflow` provenance;
- current glossary and style guide;
- current chapter source;
- for Reviewer: the current translation;
- bounded prior context required for continuity;
- exact task and expected output.

Do not load all previous chapter text merely because it exists.

When a chapter directly continues the same scene and the previous chapter is necessary to understand it, include the necessary previous context. Otherwise prefer a small ending/context excerpt plus canonical glossary/style decisions.

## Translator worker

The Translator worker produces a complete chapter draft under the literary-fidelity rules in `AGENTS.md`.

It does not mark the chapter `reviewed` and does not approve its own work.

## Reviewer worker

The Reviewer worker is independent of the Translator worker when isolated sessions are available.

It receives the source and translation and checks for omissions, additions, semantic drift, lost ambiguity, incorrect speakers, tone changes, terminology drift, formatting loss, and other failures defined by `AGENTS.md`.

Do not provide hidden reasoning from the Translator worker to the Reviewer. Review the artifact, not the Translator's justification.

## Fallback behavior

If the environment cannot create isolated workers:

- execute Translator and Reviewer as separate bounded-context roles;
- reload the required source/state for review;
- do not treat the translation pass itself as review;
- keep the orchestrator/state-management rules intact.

If the environment is read-only, explain the exact unavailable operation and the smallest manual step needed. Do not claim that files, workers, commands, or artifacts were created when they were not.

## Completion

A chapter is complete only when its translation exists, the separate source-comparison review has occurred, accepted corrections are applied, and the orchestrator has persisted the resulting state.

A book is complete only under the completion rules in `AGENTS.md`.
