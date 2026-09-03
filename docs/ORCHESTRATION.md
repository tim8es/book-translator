# Orchestration protocol

This document defines how Book Translator executes chapter work when an AI environment supports isolated workers/subagents, and how to preserve the same role boundaries when it does not.

Authority is split intentionally:

- `agent-manifest.json.contract_read_order` controls instruction-loading order;
- `AGENTS.md` controls literary quality, state meanings, review standards, and completion;
- `docs/AGENT_SETUP.md` controls version resolution, installation, and collision handling;
- this file controls execution topology, worker context boundaries, and single-writer state transitions.

## Goals

The orchestration layer exists to reduce context contamination without losing book-wide consistency.

It should:

- keep the user-facing interface unchanged;
- give translation and review fresh working contexts;
- keep book-wide decisions in durable repository state rather than long chat history;
- prevent multiple workers from racing to mutate shared state;
- remain usable in ChatGPT Web, Codex, and other capable AI agents;
- degrade cleanly when isolated workers are unavailable;
- execute each book under the workflow revision recorded for that book.

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

The Reviewer worker must not inherit hidden reasoning from the Translator worker. It reviews the source and translation artifact independently.

### Fallback: `single_agent_bounded_context`

Use when isolated workers are unavailable.

The same agent performs the roles sequentially as separate bounded-context tasks:

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

The orchestrator coordinates work without carrying an ever-growing copy of every translated chapter in conversational context.

Responsibilities:

1. identify the installation root and active book unambiguously;
2. determine the workflow revision for that book from `metadata.json.workflow` (or the active installation for a new book);
3. load the canonical contract for that revision according to `agent-manifest.json.contract_read_order`;
4. select the first chapter not `reviewed`, unless the user explicitly selects another scope;
5. build a bounded Translator context pack;
6. start/execute the Translator worker;
7. verify that a complete translation artifact exists;
8. build a bounded Reviewer context pack from durable files;
9. start/execute an independent Reviewer worker;
10. apply accepted corrections;
11. apply accepted glossary/style proposals;
12. persist `progress.json` only after the corresponding artifact/state is valid;
13. validate state;
14. continue sequentially.

## Single-writer rule

Only the orchestrator may update global mutable state during an orchestrated run.

This is a strict **single writer** rule for:

- `progress.json`;
- `glossary.md`;
- `style-guide.md`;
- book-level metadata when a worker proposes a change;
- workflow provenance when an explicit workflow upgrade is performed.

Translator and Reviewer workers may return proposed changes, but must not race to commit shared state independently.

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

- applicable rules from `AGENTS.md` loaded from the book's workflow revision;
- `metadata.json`, including `workflow` provenance;
- current `glossary.md`;
- current `style-guide.md`;
- current chapter source;
- bounded prior context needed for continuity;
- source and target language;
- expected translation path.

The Translator worker should return:

- the complete chapter translation;
- proposed glossary additions/changes, if any;
- proposed style-guide additions/changes, if any;
- explicit unresolved ambiguities or warnings, if any.

The Translator worker must not mark its own chapter `reviewed`.

## Reviewer worker

The Reviewer worker should be fresh and independent when the environment supports isolated workers.

Required context:

- applicable review rules from `AGENTS.md` from the same book workflow revision;
- current chapter source;
- current translated chapter;
- current `glossary.md`;
- current `style-guide.md`;
- bounded continuity context when required;
- expected output/correction format.

Do not pass the Translator worker's hidden reasoning or justification. The Reviewer evaluates the artifact against the source.

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
- metadata relevant to the task, including workflow provenance.

For narrative continuity:

- at a normal chapter boundary, include only the smallest prior excerpt/context required to avoid obvious continuity loss;
- if the chapter directly continues the same scene and meaning depends materially on the previous chapter, include the required previous context;
- never include unrelated chapters merely because context capacity is available.

The repository, not the orchestrator's chat memory, is the durable memory layer.

## Context freshness

A worker result is valid only for the state it was given.

When the environment can identify revisions/hashes, associate the worker task with:

- the book's `metadata.json.workflow.resolved_revision`;
- current book/progress state;
- current glossary/style state.

If shared state changes materially before a worker result is accepted, rebuild or re-check that result against the new state rather than accepting it blindly.

## Resume behavior

On a new session:

1. identify the installation root;
2. enumerate valid book workspaces;
3. if the user explicitly identifies a book, select it;
4. otherwise, if exactly one incomplete book exists, select it;
5. if multiple incomplete books exist and user intent does not identify one, ask only which book to resume;
6. read that book's `metadata.json.workflow` before selecting the execution contract;
7. load the recorded workflow revision according to its `agent-manifest.json.contract_read_order`;
8. load `progress.json`, `glossary.md`, and `style-guide.md`;
9. continue with the first chapter not `reviewed`.

Do not choose arbitrarily between multiple incomplete books.

## Revision policy

For a **new book**, use the currently selected/resolved workflow revision and persist it into `metadata.json.workflow` at initialization.

For an **existing book**, the book-level `metadata.json.workflow` is the provenance source for its execution contract.

`<install_root>/.book-translator-install.json` describes the currently installed workflow, but it does not override an existing book's recorded revision.

If the installed revision and book revision differ:

- do not silently rewrite book provenance;
- load the book's recorded workflow revision for the run when the environment permits it;
- otherwise state the exact reproducibility limitation before making state-changing claims;
- perform an upgrade only as an explicit transition followed by compatibility/validation checks.

Different books in one workspace may legitimately retain different workflow revisions.

## Failure handling

If a Translator worker fails, do not advance chapter state.

If a Reviewer worker fails, keep the translation at `translated`; do not mark it `reviewed`.

If global-state validation fails, stop advancement, repair repository state, and re-run validation before starting the next chapter.

If isolated workers are unavailable, switch to the documented bounded-context fallback rather than asking the user to manage agents manually.

If the required workflow revision for an existing book cannot be loaded, do not silently substitute a different revision and claim exact reproducibility.

## Completion

The orchestration layer does not change the definition of `reviewed` or book completion.

It ensures that translation, independent review, state transitions, and workflow provenance remain separated and reproducible.
