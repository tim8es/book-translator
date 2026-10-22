# Manual Chat Session Runtime and Benchmark

The user opens ChatGPT conversations independently; no agent spawning, external database, implicit shell execution, or LLM API is required.

## Durable recovery

Verify source coverage and integrity before dispatch. Generate workpacks:

    python scripts/book.py plan-sessions novel --sessions 3 --save --json

The response contains a dispatch ID, and separate session IDs for each user-opened chat. Persisting creates an immutable file at .workflow/session-dispatches/<dispatch-id>.json. Without --save, planning remains read-only; a conflicting dispatch ID cannot overwrite an earlier saved plan.

In a new or restarted chat, query the next step:

    python scripts/book.py session-next novel --dispatch-id <id> --session-id <id> --json

The read-only command recomputes the current next step from progress, claims, frozen glossary/style revisions, review ledger and verified source state. It returns claim (acquire the specified role's durable claim), work (only when the live claim is held by this session), wait (review/correction/promotion belongs to another role), blocked (unsafe current state), or complete (all assigned units are reviewed). Never treat this dispatch as permission to modify files. Claim, accept and release each unit using the existing orchestrator protocol. Translator and Reviewer roles must remain distinct.

There is no duplicate progress state or fabricated checkpoint. Only accepted translations, hash-bound reviews, and current reviewed states survive as authoritative checkpoints. An expired claim, changed source edition, changed reading order, or shared-state
revision drift blocks instead of silently reviving stale work.

When only GitHub tools are available, the stored dispatch JSON and state can be inspected through GitHub. This Python CLI does not create chat sessions or claim to run within ChatGPT without executable tools.

## Comparable performance experiments

Evaluate your own measured observations:

    python scripts/book.py benchmark-sessions observations.json --json

Record data in observations.json, using the following schema. The numbers here are illustrative, NOT experimental findings:

    {
      "schema_version": 1,
      "dataset_id": "novel-trial-001",
      "evaluation_protocol": "blind-review-v1",
      "runs": [
        {
          "run_id": "solo", "sessions": 1,
          "unit_ids": ["chapter-000001"],
          "reviewed_unit_ids": ["chapter-000001"],
          "source_words": 900, "elapsed_seconds": 3600,
          "github_requests": 50, "manual_interventions": 7,
          "major_findings": 1, "critical_findings": 0
        },
        {
          "run_id": "parallel", "sessions": 2,
          "unit_ids": ["chapter-000001"],
          "reviewed_unit_ids": ["chapter-000001"],
          "source_words": 900, "elapsed_seconds": 1700,
          "github_requests": 38, "manual_interventions": 4,
          "major_findings": 1, "critical_findings": 0
        }
      ]
    }

The analyzer rejects nonidentical chapter sets/word counts, missing independent review, duplicate runs, invalid metrics, or missing single-chat baseline. For repeated trials it uses medians and reports reviewed words/hour, GitHub calls, user interventions, and major/critical issues. The provisional 2x throughput indicator is not proof of statistically significant literary improvement. Keep the source corpus, independent blind reviewer and evaluation rubric fixed across conditions.

The benchmark remains observation-driven: full ChatGPT session time and ChatGPT connector calls are not automatically measured. The optional Python measurements below cover only instrumented runtime activity.

## Optional runtime measurements

Set `BOOK_TRANSLATOR_METRICS_FILE` to a local JSONL path **outside the book workspace**
before running Python commands. The CLI records one event per invocation with
the command name, exit code and measured execution time. When the GitHub REST
client is used in the same instrumented runtime, each HTTP request also records
its method, outcome and elapsed time; sensitive URLs, headers and bodies are
not logged. The recorder makes no extra GitHub requests or changes to book data.

Events are appended by Python, not authored or estimated by the AI agent.
This measures only instrumented Python execution: it does **not** measure model
thinking time, prompt/completion tokens, requests made by ChatGPT's own GitHub
connector, or full chat-session wall time. Those require separate independently
observable data sources. Keep the file in a trusted local directory; optional
measurement failure must not block translation.
