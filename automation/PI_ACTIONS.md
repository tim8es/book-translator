# Pi in GitHub Actions — opt-in Auto backend

The canonical Book Translator workflow remains unchanged. Pi is a model
execution backend for the existing bounded Auto runner, not a second state
machine and not a replacement for source-coverage, claim, hash, review or EPUB
checks. Each Translator, Reviewer, Corrector and Orchestrator call has its own
tool-free, in-memory Pi session. It cannot use Bash, Git or filesystem tools.
The pinned book CLI and current Auto runner own every durable mutation.

## Deployment

- Public implementation: automation/pi_backend.py and the optional dispatch
  in automation/omniroute.py.
- Private state repository: a reusable manual-only Action checks out one
  immutable public runtime commit containing these files.
- BOOK_TRANSLATOR_LLM_BACKEND=pi selects Pi for that one Action; the existing
  scheduler continues to use the original omniroute backend by default.
- @earendil-works/pi-coding-agent@1.6.0 is installed on the runner with
  Node.js 22.19+. It is not a dependency of ChatOps or ordinary Auto.
- Every book still uses books/<slug>/metadata.json.workflow.resolved_revision
  for its literary workflow contracts; this executor ref does not upgrade books.

Pi receives only the role-specific system and user prompt assembled by the
existing Auto code. It uses a temporary config directory, --no-tools,
--no-session, and --no-approve. The only secret passed into its process is
OMNIROUTE_API_KEY; the GitHub token and other environment variables are
excluded. Pi retries, prompt compaction and cache warming are disabled
because the Auto runner owns the durable model-call budget and retry policy.
The model must return JSON matching the existing strict Auto schemas.
Pi print mode does not enforce the OpenAI json_object response format,
so compatibility must be tested against the configured OmniRoute route.

## Configuration in the private Actions repository

Existing settings are reused:

- Secret OMNIROUTE_API_KEY;
- variables OMNIROUTE_BASE_URL and
  OMNIROUTE_TRANSLATOR_MODEL, OMNIROUTE_REVIEWER_MODEL,
  OMNIROUTE_CORRECTOR_MODEL, OMNIROUTE_ORCHESTRATOR_MODEL.

Optional: PI_AGENT_CONTEXT_WINDOW=131072 and
PI_AGENT_MAX_TOKENS=32768. Choose values supported by the actual model.

## Safe test and release

1. Open the new private Pi book translation (manual pilot) Action and
   run probe_only=true. This tests installation, version pin and offline
   unit tests without credentials, model calls or repository writes.
2. Prepare a new synthetic or permissioned test job using the ordinary
   intake and book initialization path. Do not reuse a blocked or partially
   translated book; do not change any existing book workflow revision.
3. With the existing Auto mode paused for that test book, dispatch Pi with
   the job ID, continue_automatically=false. Observe the durable
   automation/status/<job-id>.json, selected chapter state, and Action logs.
4. After a successful canonical checkpoint and independent review, test the
   explicit continuation option. The existing final EPUB conditions still
   apply. Do not mark a book done when any unit needs attention.

This is an experimental alternate model transport, not evidence of a
successful full-book translation. The existing Auto schedule and ChatOps
remain untouched. A Pi error produces a sanitized model failure and cannot
bypass literary checks. Run no concurrent Auto/Pi jobs for the same book.
