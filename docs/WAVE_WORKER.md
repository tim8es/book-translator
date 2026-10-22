# Active wave — worker contract

This short overlay applies only when the selected book has an ACTIVE wave.
Use it **in addition to** `AGENTS.md`, the shared
`docs/TRANSLATION.md` standard, and the current role's
`docs/TRANSLATOR.md` or `docs/REVIEWER.md` contract, selected from
the same pinned workflow revision. Do not load the Orchestrator's entire workflow or unrelated roles.

1. Receive the exact dispatch ID and session ID for this chat. Call
   `wave-next <book> --dispatch-id <id> --session-id <id>` when an
   executable runtime is available. The returned **load_plan** lists
   references only, not the source or translation text.
2. Stop on `blocked`, `wait`, or `complete`. Never treat a workpack as
   permission to write. For `claim`, acquire the indicated role's live
   durable claim **before** reading the full chapter working pack.
   For `work`, verify that the existing matching claim is still live.
3. Load the instructions for the **current role** and the exact referenced
   glossary/style, original source, and pinned literary-memory snapshot.
   Generate the bounded `memory-context` for the current unit and verify
   its revision equals the wave's pinned memory revision. A translation or
   revision/correction also reads the current target and review ledger when
   instructed. Read the whole original chapter when context permits; if
   chunked, preserve sequence and complete coverage.
4. Translator saves a full draft and obtains normal acceptance. Reviewer
   compares the exact source and accepted translation independently, records
   supported findings, and never marks their own translation as reviewed.
   Continue with existing claim release and Orchestrator promotion.
5. Before any subsequent chapter, re-evaluate `wave-next` against current
   GitHub state. Reuse unchanged role instructions already loaded in this
   conversation; replace chapter-level inputs with the next unit's current
   inputs. On a changed role, discard the old role's working context and
   load the newly selected role contract.
6. Treat any source/workflow/shared-memory drift, expired claim, missing
   source coverage or unsupported repository operation as blocking. The
   GitHub connector does not execute Python merely because this contract
   names a command: use verified tools or fail closed.

Do not read `docs/WAVES.md`, `AGENT_SETUP.md`, or other long contracts
by default as a worker. The pinned Orchestrator owns wave reconciliation.
