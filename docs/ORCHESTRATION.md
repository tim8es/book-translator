# Orchestration protocol

This document defines how Book Translator should execute chapter work when an AI environment supports isolated workers/subagents, and how to preserve the same role boundaries when it does not.

`AGENTS.md` remains authoritative for literary quality, state meanings, and completion. This document controls execution topology and context boundaries.

## Goals

The orchestration layer exists to reduce context contamination without losing book-wide consistency.

It should:

- keep the user-facing interface unchanged;
- give translation and review fresh working contexts;
- keep book-wide decisions in durable repository state rather than long chat history;
- prevent multiple workers from racing to mutate shared state;
- remain usable in ChatGPT Web, Codex, and other capable AI agents;
- degrade cleanly when isolated workers are unavailable.

## Execution modes

### Preferred: `isolated_workers`

Use when the active environment can create independent worker sessions or subagents.

For each chapter:

```text
Orchestrator
  -> Translator worker (fresh session)
  -> Reviewer worker (fresh, independent session)
  -> Orchestrator accepts/corrects state proposals
  -> Persist global state
  -> Next chapter
```

The Reviewer worker must not inherit hidden reasoning from the Translator worker. It reviews the source and artifact independently.

### Fallback: `single_agent_bounded_context`

Use when isolated workers are unavailable.

The same agent performs the roles sequentially, but treats each role as a separate bounded-context task:

1. load the Translator context pack and produce the draft;
2. finish the translation role;
3. reload the Reviewer context pack from durable files;
4. independently compare source and translation;
5. return review findings/corrections;
6. execute the orchestrator state transition.

Do not collapse translation and review into one pass merely because only one physical agent is available.

## User-facing invariant

The user does not need to request or understand subagents.

The normal interface remains:

1. Book Translator repository/skill;
2. source book;
3. target language.

Capability detection and execution-mode selection are internal responsibilities of the active agent.

## Orchestrator

The orchestrator coordinates work. It should not carry an ever-growing copy of every translated chapter in its conversational context.

Responsibilities:

1. load the canonical workflow revision and book state;
2. choose the active book unambiguously;
3. select the first chapter not `reviewed`, unless the user explicitly selects another scope;
4. build a bounded Translator context pack;
5. start/execute the Translator worker;
6. verify that a complete translation artifact exists;
7. build a bounded Reviewer context pack from durable files;
8. start/execute an independent Reviewer worker;
9. apply accepted corrections;
10. apply accepted proposed glossary/style decisions;
11. persist `progress.json` only after the corresponding artifact/state is valid;
12. validate state;
13. continue sequentially.

## Single-writer rule

Only the orchestrator may update global mutable state during an orchestrated run.

This is a strict **single writer** rule for:

- `progress.json`;
- `glossary.md`;
- `style-guide.md`;
- workflow/install provenance;
- book-level metadata when a worker proposes a change.

Translator and Reviewer workers may return proposed changes, but should not race to commit them independently.

Translation artifacts may be written by a worker only when the active environment gives that worker an isolated/non-conflicting target. The orchestrator remains responsible for accepting the artifact into canonical state.

## Sequential chapter policy

Translate chapters sequentially by default:

```text
T1 -> R1 -> state commit -> T2 -> R2 -> state commit -> T3 ...
```

Do not translate multiple chapters concurrently by default. Later chapters may depend on terminology, character voice, ambiguity, or continuity decisions established during earlier reviewed chapters.

Parallelism may be introduced only by an explicit future mode with conflict handling and state-version checks. It is not part of the default contract.

## Translator worker

The Translator worker receives a fresh task for one chapter.

Required context:

- applicable rules from `AGENTS.md`;
- `metadata.json`;
- current `glossary.md`;
- current `style-guide.md`;
- the current chapter source;
- bounded prior context needed for continuity;
- source and target language;
- expected translation path;
- current workflow revision when available.

The Translator worker should return:

- the complete chapter translation;
- proposed glossary additions/changes, if any;
- proposed style-guide additions/changes, if any;
- explicit unresolved ambiguities or warnings, if any.

The Translator worker must not mark its own chapter `reviewed`.

## Reviewer worker

The Reviewer worker should be fresh and independent when the environment supports isolated workers.

Required context:

- applicable review rules from `AGENTS.md`;
- the current chapter source;
- the current translated chapter;
- current `glossary.md`;
- current `style-guide.md`;
- bounded continuity context when required;
- expected output/correction format.

Do not pass the Translator worker's hidden reasoning or justification. The Reviewer should evaluate the artifact against the source.

The Reviewer checks at minimum:

- completeness;
- additions;
- semantic drift;
- factual/causal/chronological errors;
- negation and modality;
- ambiguity and subtext;
- speaker/viewpoint identification;
- emotional intensity and tone;
- character voice;
- terminology consistency;
- meaningful formatting;
- accidental translator-created awkwardness.

The Reviewer returns corrections/findings and any proposed global-state updates. The orchestrator decides and persists accepted state.

## Bounded context policy

Do not give workers the entire conversation or every previous chapter by default.

Always include durable book-wide decisions:

- glossary;
- style guide;
- metadata relevant to the task.

For narrative continuity:

- if the chapter is a normal boundary, include only the smallest prior excerpt/context required to avoid obvious continuity loss;
- if the chapter directly continues the same scene and meaning depends materially on the previous chapter, include the required previous chapter context;
- never include unrelated chapters merely because context capacity is available.

The repository, not the orchestrator's chat memory, is the durable memory layer.

## Context freshness

A worker result is valid only for the state it was given.

When the environment can identify revisions/hashes, the orchestrator should associate the worker task with:

- resolved workflow revision;
- current book/progress state;
- current glossary/style state.

If shared state changes materially before a worker result is accepted, rebuild or re-check that result against the new state rather than accepting it blindly.

## Resume behavior

On a new session:

1. read `.book-translator-install.json` when present;
2. read `AGENTS.md` and this orchestration protocol;
3. enumerate valid book workspaces under `books/`;
4. if exactly one active/incomplete book exists, select it;
5. if the user explicitly identified a book, select that one;
6. if multiple incomplete books exist and user intent does not identify one, ask only which book to resume;
7. load that book's metadata/progress/glossary/style guide;
8. continue with the first chapter not `reviewed`.

Do not choose arbitrarily between multiple incomplete books.

## Revision pinning

When the user does not pin a version, `latest main` is resolved once at setup.

In a writable workspace record the resolved canonical revision in `.book-translator-install.json` when the environment can resolve it.

Do not silently update to a newer `main` halfway through a book. An upgrade should be explicit and followed by compatibility validation.

If the environment cannot resolve a concrete revision, record the most specific source/ref information it can and state that exact reproducibility is unavailable in that environment.

## Failure handling

If a Translator worker fails, do not advance chapter state.

If a Reviewer worker fails, keep the translation at `translated`; do not mark it `reviewed`.

If global-state validation fails, stop advancement, repair repository state, and re-run validation before starting the next chapter.

If isolated workers are unavailable, switch to the documented fallback rather than asking the user to manage agents manually.

## Completion

The orchestration layer does not change the definition of `reviewed` or book completion.

It only ensures that translation, independent review, and global-state transitions remain separated and reproducible.
