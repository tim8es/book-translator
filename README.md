# Book Translator

**Translate full books with an AI agent while keeping progress, terminology, style, review state, and source integrity between sessions.**

Book Translator is designed primarily for **ChatGPT Web** and **Codex**, while remaining **agent-agnostic** so other capable AI agents can use the same workflow.

**No programming knowledge required. No API key required.** You can start by giving an AI agent a repository link, a book, and the language you want.

> Repository: `https://github.com/tim8es/book-translator`

> **README is a human-facing overview and is not part of the agent execution contract.** The executable routing source is `agent-manifest.json`; role-specific rules live in the contracts referenced there.

## Start in 30 seconds

If you already have a capable AI agent open:

1. attach or otherwise give it access to your book;
2. paste this message;
3. change `Russian` to the language you want.

```text
Use https://github.com/tim8es/book-translator to translate the attached book into Russian.
Set up everything you can automatically, follow the repository workflow,
keep durable progress when the environment allows it, and review the translation against the original.
```

That is enough to start. You do not need to understand Git, Python, repositories, subagents, chapter states, glossaries, or the internal review system first.

## Choose how to use it

There are three practical ways to use Book Translator.

| Mode | Best for | Persists between sessions? | Technical setup |
| --- | --- | --- | --- |
| **Web AI** | Trying Book Translator or work that can reasonably finish in the current AI workspace | Only if that web environment actually provides persistent files/workspace | None if the agent can prepare its own workspace |
| **Private GitHub workspace** | **Full-book and multi-session translations** | Yes, when changes are saved to the repository | Usually the agent can prepare it; manual steps are available below |
| **Local workspace** | Maximum local control/privacy, Codex and other coding agents | Yes, on your computer | One clone command, or ask an agent to do it |

**Option 2 is recommended for full-book and multi-session translations.**

Do not choose a mode by a fixed number of chapters. A five-chapter book can be larger than a thirty-chapter book. The important question is whether you expect the work to outlive the current AI session or temporary workspace.

## Option 1 — Use it directly in a Web AI

This is the easiest way to try Book Translator.

### What you do

1. Open ChatGPT Web or another capable AI web interface.
2. Attach the book.
3. Send the prompt from [Start in 30 seconds](#start-in-30-seconds).

If the AI environment provides a writable cloud workspace, repository access, or an equivalent virtual filesystem, the agent can prepare Book Translator there and perform the translation workflow inside that environment.

### When to use this mode

Use it when:

- you are testing the workflow;
- the requested scope can reasonably finish in the current session/workspace;
- you do not need to rely on that temporary environment as your long-term source of truth.

### Important limitation

A web AI does **not** automatically imply a permanent virtual machine or permanent filesystem. Capabilities differ by product and session.

Book Translator should use persistence when it is actually available, but it must not pretend a temporary workspace will survive after the session ends.

For a long-running book, use a persistent workspace instead.

## Option 2 — Use a private GitHub workspace

**Recommended for full-book and multi-session translations.**

A private GitHub repository gives the translation a durable home. The book can be continued tomorrow, in another chat, on another computer, or by another capable AI agent that can access the same repository.

### Easiest method: ask your agent to set it up

If your AI agent can work with GitHub, give it a request like:

```text
Create or prepare a private GitHub workspace for my book translations.
Install Book Translator from https://github.com/tim8es/book-translator there.
Keep each book in its own books/<book-slug>/ workspace.
Then translate the attached book into Russian and persist progress so another session can resume it.
```

The agent should use only GitHub/repository capabilities that are actually available. If it cannot create a repository or push changes, it should tell you the smallest manual step required instead of claiming the setup was completed.

### Manual fallback

If you prefer to create the private repository yourself:

1. On GitHub, create a new **private** empty repository, for example `my-book-translations`.
2. Clone Book Translator to your computer:

```bash
git clone https://github.com/tim8es/book-translator.git my-book-translations
cd my-book-translations
```

3. Point the working copy at your new private repository and push it:

```bash
git remote rename origin book-translator
git remote add origin https://github.com/YOUR-NAME/my-book-translations.git
git push -u origin main
```

Replace `YOUR-NAME` with your GitHub username.

The original Book Translator repository remains available as the `book-translator` remote, while your books and translation state live in your private `origin` repository.

After that, open the private repository with your AI agent and say:

```text
Translate the attached book into Russian using the Book Translator workflow in this repository.
```

## Option 3 — Use it on your computer

This is a good option for Codex and other coding agents that can work directly with your local files.

### Easiest method: ask the agent

Tell your coding agent:

```text
Clone https://github.com/tim8es/book-translator to my computer,
prepare it as a persistent book-translation workspace,
and use it to translate the attached book into Russian.
```

### Manual fallback

If you have Git installed, the setup is:

```bash
git clone https://github.com/tim8es/book-translator.git
cd book-translator
```

Then open that folder with your agent and give it the book plus the target language.

A local workspace persists independently of a chat session. You can back it up, keep it private, or connect it to a private GitHub repository later.

## One workspace can contain many books

You do **not** need a separate Book Translator installation for every book.

One persistent workspace can contain many books side by side:

```text
books/
├── good-intentions/
├── my-first-novel/
└── another-book/
```

Each book has its own durable state:

```text
books/<book-slug>/
├── source/              # preserved original
├── extracted/           # extracted source chapters/sections
├── translated/          # translated chapters
├── output/              # assembled deliverables
├── metadata.json        # book metadata + workflow provenance
├── progress.json        # durable chapter queue
├── source-manifest.json # source/extraction integrity when sealed
├── glossary.md          # recurring terminology/continuity decisions
└── style-guide.md       # evidence-based literary observations
```

The book folder is the primary unit of durable book state.

### Do I need a Git branch for every book?

No. A permanent branch per book is **not** required and is not the beginner default.

Keeping books under separate `books/<book-slug>/` folders is simpler: the agent can see all available books, resume the right one, and keep workflow updates in one workspace.

Advanced users or agents may use temporary branches/worktrees when they need isolation for concurrent work. That is an implementation technique, not something a normal user must manage.

If one book needs stronger privacy or organizational isolation, putting that book in its own private repository is usually easier to understand than using a permanent branch as storage.

## How resuming works

Book Translator stores the state needed to continue in files, not only in chat history.

For example:

**Today:**

```text
Translate Good Intentions into Russian.
```

The agent translates/reviews chapters and records progress.

**Tomorrow, in a new chat:**

```text
Continue translating Good Intentions from the repository state.
```

The agent can inspect the saved book workspace, determine what has already been completed, and continue from the next required operation instead of depending on yesterday's conversation.

This is why a persistent GitHub or local workspace is recommended for serious full-book work.

## What Book Translator does for you

At a high level, the system is designed to:

1. preserve the original book;
2. extract its real reading order into manageable source units;
3. create durable per-book progress and metadata;
4. build a glossary and evidence-based style guide as the book develops;
5. translate one chapter/unit at a time;
6. review each translation against the original in a separate review role;
7. keep shared decisions and progress consistent;
8. detect structural/source-corpus problems instead of silently translating from incomplete inputs;
9. resume later from repository state;
10. assemble the reviewed translation into an output when requested.

The literary goal is fidelity rather than rewriting: meaning, ambiguity, tone, voice, character distinctions, rhythm, subtext, repetition, and meaningful formatting are all part of the review model.

## Privacy and copyright

For copyrighted, unpublished, confidential, or otherwise private books, use a **private GitHub repository** or a **local private workspace** unless you have the right and intention to publish the material.

The canonical Book Translator repository contains the workflow, not bundled real books. Your real source books belong in your chosen private working environment.

A temporary web-AI upload or workspace is subject to the capabilities and data handling of that product; Book Translator itself does not create a separate storage/privacy guarantee on top of the host environment.

## Frequently asked questions

### Do I need programming knowledge?

No. The intended beginner path is to give the repository link and book to a capable AI agent and let the agent handle technical setup where its environment permits it.

### Do I need Python?

No. Python helpers automate extraction, validation, integrity, recovery, and build tasks when available, but Python is not required to understand the literary workflow. If a capability is unavailable, the active agent should use the documented fallback or tell you the minimum manual step required.

### Do I need an API key?

No API key is required by the default Book Translator workflow. The AI product you use may of course have its own account/subscription requirements.

### Do I need Git?

Not necessarily. For a simple Web AI trial, no Git knowledge is required. For persistent GitHub/local use, an AI coding agent can often manage Git for you. Manual copy/paste commands are provided as a fallback.

### Can I translate several books in one repository?

Yes. One workspace can contain many books under `books/<book-slug>/`, each with its own progress, source, glossary, style guide, provenance, and output.

### Can I close the chat and continue later?

Yes **when the book state is stored in a persistent workspace** such as a private GitHub repository or local folder. Do not rely on an ephemeral web workspace unless the product explicitly preserves it.

### Can another AI agent continue the same translation?

Yes, if it can access the same Book Translator workflow revision and the durable book state. The workflow is designed not to require previous chat history for resume.

### Can I use PDF or DOCX?

Potentially. EPUB, HTML/XHTML, Markdown, and TXT have automatic support in the included standard-library helper. PDF and DOCX depend on whether the active agent/environment can read or extract them reliably.

### Should my working repository be private?

Usually yes for copyrighted, unpublished, or personal books. Use a public repository only when you have the rights and explicitly want the source/translation state to be public.

### Should I create one branch per book?

No. Use separate book folders by default. Branch/worktree isolation is optional for advanced concurrent workflows, not a required storage model.

---

# How it works internally

Everything below is technical reference. You do not need it to start translating.

## Role-routed agent contract

A full-book translation needs different kinds of reasoning. Loading setup, orchestration, literary, and state-management instructions into every worker wastes context and can mix responsibilities.

Book Translator therefore routes each role to a focused instruction set:

```text
agent-manifest.json
       │
       ├─ bootstrap    → AGENTS.md + docs/AGENT_SETUP.md
       ├─ orchestrator → AGENTS.md + docs/ORCHESTRATION.md
       ├─ translator   → AGENTS.md + docs/TRANSLATION.md
       └─ reviewer     → AGENTS.md + docs/TRANSLATION.md
```

`AGENTS.md` contains global invariants that apply to every role. Setup instructions do not travel into translation context, and detailed literary review criteria do not travel into setup context.

## Contract responsibilities

| File | Purpose |
| --- | --- |
| `agent-manifest.json` | Machine-readable contract registry, context profiles, stable defaults, provenance locations, and source-format capabilities. |
| `SKILL.md` | Thin one-link discovery/bootstrap entrypoint. |
| `AGENTS.md` | Small global invariant layer safe to auto-load. |
| `docs/AGENT_SETUP.md` | Ref/version resolution, capabilities, workspace/install selection, collisions, and installation provenance. |
| `docs/ORCHESTRATION.md` | Book initialization/resume, role dispatch, bounded context, chapter state, single-writer persistence, validation, corpus preflight, and completion sequencing. |
| `docs/TRANSLATION.md` | Authoritative literary translation and independent source-comparison review contract. |

README remains human-facing; normative execution behavior lives in those contracts.

## Translation and review model

The Translator and Reviewer are separate logical roles.

```text
Orchestrator
  → Translator
  → Reviewer
  → Orchestrator accepts valid state
  → next chapter
```

When independent workers/subagents are available, they can use separate bounded contexts. When they are unavailable, one physical agent can execute the same roles sequentially while rebuilding context from durable files.

The Reviewer compares source and translation. The Orchestrator owns durable shared-state transitions.

## Durable state and source integrity

`progress.json` tracks chapter work. `metadata.json.workflow` records the workflow provenance associated with the book. `glossary.md` and `style-guide.md` hold book-wide continuity decisions.

When the source corpus is sealed, `source-manifest.json` records SHA-256 identities for the preserved source and extracted artifacts. This allows a later session to detect an incomplete/changed source corpus rather than treating whatever files happen to remain as authoritative.

## Supported source formats

The included standard-library helper can automatically extract:

- EPUB;
- HTML / XHTML;
- Markdown;
- TXT.

DOCX and PDF can use the same durable workflow when the active agent can read/extract them reliably, but the included helper does not claim automatic extraction for those formats.

For EPUB, reading order follows the package/spine.

## Optional local CLI

The Python helpers perform structural operations; they do not call an LLM API and do not replace literary review.

Typical commands:

```bash
python scripts/book.py extract /path/to/book.epub --target-language ru
python scripts/book.py validate <book-slug>
python scripts/book.py build <book-slug>
```

Source-corpus integrity/recovery helpers include:

```bash
python scripts/corpus.py seal <book-slug>
python scripts/corpus.py restore <book-slug> /path/to/original-source
```

The helpers use the Python standard library.

## Project structure

```text
.
├── agent-manifest.json       # role/context router
├── SKILL.md                  # discovery entrypoint
├── AGENTS.md                 # global invariants
├── books/                    # per-book durable state
├── docs/
│   ├── AGENT_SETUP.md        # technical setup authority
│   ├── ORCHESTRATION.md      # execution/state authority
│   ├── TRANSLATION.md        # literary translation/review authority
│   └── templates/            # state templates
├── scripts/
│   ├── book.py               # extraction/validation/build helper
│   └── corpus.py             # source-integrity/recovery helper
└── tests/
```

Design and implementation records for major architectural changes may also live under `docs/superpowers/`; they are development records, not runtime contracts.

## Tests

Run the complete test suite with:

```bash
python -m unittest discover -s tests -v
```

Contract tests protect role routing and documentation boundaries. CLI/corpus tests protect extraction, provenance, validation, source integrity/recovery, and build behavior.

## License

MIT. See [`LICENSE`](LICENSE).
