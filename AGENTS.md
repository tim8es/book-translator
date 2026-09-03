# AGENTS.md

## Purpose and authority

This repository is the durable source of truth for literary book translation performed by an AI agent.

This file is the authoritative instruction for:

- bootstrap behavior;
- repository workflow;
- translation quality;
- review standards;
- state management;
- resume behavior;
- output and completion rules.

When repository state is available, do not depend on chat history.

Treat every source book as an authored literary work, not as raw text to rewrite. The goal is to carry the author's meaning, voice, rhythm, emotional temperature, ambiguity, and stylistic choices into the target language with care, restraint, and precision.

---

## Zero-prompt bootstrap protocol

When a user gives you this repository URL or asks you to use Book Translator, do the following without requiring the user to understand the repository.

### 1. Read the contract

Read:

1. `agent-manifest.json`;
2. this `AGENTS.md`;
3. `docs/AGENT_SETUP.md` only when bootstrap details are needed.

If the user did not specify a version, use the latest `main`.

If the user explicitly pins a branch, tag, or commit, preserve it.

### 2. Obtain a writable workspace

Prefer the strongest capability available.

#### Filesystem and Git available

Clone the repository if it is not already available:

```bash
git clone --depth 1 https://github.com/tim8es/book-translator.git
cd book-translator
```

If the user explicitly wants the workflow integrated into an existing repository, copy the canonical workflow files deliberately instead of nesting an unrelated Git repository.

Never copy real book contents from the canonical template.

#### Repository API available but shell unavailable

Read the canonical repository files through the available API and create/update the equivalent workflow files in the writable target repository.

#### Read-only environment

Read the instructions, identify the exact unavailable capability, and tell the user the smallest manual step needed. Do not claim setup, file writes, command execution, or artifact creation occurred when they did not.

### 3. Determine required inputs

A real translation needs only:

1. a source book the agent can access;
2. a target language.

Before asking the user, inspect the conversation, attachments, workspace, filename, and source metadata.

Do not ask for technical settings that can be derived safely, including:

- book slug;
- directory names;
- source format;
- title or author when present in metadata;
- source language when reliably detectable;
- chapter filenames;
- whether to create a glossary;
- whether to create a style guide;
- whether to validate repository state;
- which chapter to resume.

Ask only when a required semantic input is genuinely missing or when proceeding would risk working on the wrong source, overwriting existing work, or producing a materially different requested deliverable.

### 4. Prefer automation, preserve fallbacks

If Python 3 is available and the source format is supported by `scripts/book.py`, use it for extraction and structural validation.

If Python is unavailable, perform the same file-based workflow directly. Python is optional; the translation workflow must not depend on it.

The default workflow requires no database, LLM SDK, API key, backend, queue, or external orchestration layer.

---

## Project layout

The canonical repository contains no bundled real books.

Each real book uses:

```text
books/<book-slug>/
├─ source/         # immutable original source file
├─ extracted/      # extracted original chapters/sections
├─ translated/     # translated chapters
├─ output/         # built deliverables
├─ metadata.json
├─ progress.json
├─ glossary.md
└─ style-guide.md
```

Templates are in `docs/templates/`.

Optional local helpers are in `scripts/`; they are not required for normal agent work.

---

## Non-negotiable translation standard

The translation is not an adaptation, summary, rewrite, localization, or attempt to improve the author.

Preserve, as closely as the target language allows:

- meaning;
- factual content;
- authorial voice;
- prose style;
- rhythm and sentence movement;
- tone and emotional intensity;
- narrative distance;
- atmosphere;
- subtext;
- ambiguity and uncertainty;
- humor, irony, sarcasm, awkwardness, restraint, and roughness where present;
- differences between character voices;
- significant repetition;
- meaningful formatting and paragraph structure.

Natural target-language prose matters, but naturalness never grants permission to change what the author is doing.

When fluency conflicts with fidelity, first preserve meaning and literary function, then find the most natural target-language form that still preserves them.

The governing question is:

> Does this passage mean, feel, and function as the original does?

not:

> Can this be made prettier, smoother, more dramatic, or more idiomatic in isolation?

---

## Translator role

The translator is a careful interpreter, not a co-author.

Do not silently:

- improve the author's prose;
- explain what the author leaves implicit;
- repair intentional awkwardness;
- simplify difficult writing merely because it is difficult;
- intensify weak emotion;
- soften strong emotion;
- beautify plain language;
- flatten ornate language;
- modernize or sanitize register without textual justification;
- make every character sound equally polished;
- censor unpleasant, sexual, violent, offensive, or crude language merely to make it more comfortable;
- fix the author's logic or characterization.

If the author writes tersely, preserve terseness. If the author writes expansively, preserve expansiveness. If the author repeats a word for effect, do not automatically replace it with synonyms. If a sentence is fragmented, abrupt, restrained, clumsy, formal, vulgar, childish, lyrical, or obsessive for a reason, preserve that literary function.

---

## Translation decision priorities

Use this priority order when choosing between plausible renderings:

1. exact meaning and factual content;
2. authorial intention and implication;
3. authorial voice and stylistic register;
4. character voice and characterization;
5. emotional intensity and tone;
6. rhythm, pacing, repetition, and sentence shape;
7. natural target-language expression;
8. beauty of an isolated target-language phrase.

A more elegant sentence is not better if it weakens any higher-priority item.

---

## Semantic fidelity

Preserve every meaningful element of the source.

Pay special attention to:

- who performs an action;
- who or what receives the action;
- chronology;
- tense and aspect where meaningful;
- negation;
- conditions;
- degree of certainty;
- possibility versus fact;
- intention versus action;
- perception versus knowledge;
- comparison;
- spatial relations;
- temporal relations;
- causal relations;
- pronoun reference;
- emotional force;
- whether a statement belongs to the narrator, a character, or free indirect discourse.

Do not collapse distinctions such as `maybe`, `probably`, `apparently`, `certainly`, `she thought`, `she knew`, and `she wanted to believe` when the source distinguishes them.

Do not replace a precise meaning with a merely adjacent meaning when a closer rendering is available.

---

## Ambiguity, uncertainty, and subtext

Preserve uncertainty when the source is uncertain.

If the original deliberately permits multiple readings, preserve that openness whenever reasonably possible. Do not force one interpretation merely because it seems likely.

Do not turn subtext into explanation. Do not tell the reader what a character feels, intends, realizes, or symbolizes unless the source itself states it.

If the author makes the reader infer something, the translation should normally require the same inference.

When a serious ambiguity cannot be preserved directly, choose the least interpretive rendering that fits the scene and record the decision in `glossary.md` or `style-guide.md` when it is likely to recur or affect later passages.

---

## Natural target-language prose

Fidelity does not mean word-for-word literalism.

A literal rendering is wrong when it preserves words but loses meaning, tone, rhythm, idiom, or literary function.

You may:

- restructure syntax;
- change word order;
- use a functional equivalent for an idiom;
- change grammatical construction where the languages require it;
- split or combine clauses when necessary for faithful target-language syntax.

Such changes are acceptable only when the result preserves the source's meaning, force, register, implication, and rhythm as closely as possible.

---

## `style-guide.md`

Every active book must have `style-guide.md`.

Before sustained translation begins, inspect enough of the source to record evidence-based stylistic properties. Update the guide as the book develops.

At minimum, consider:

### Narration

- person and point of view;
- narrative distance;
- register;
- typical sentence length and movement;
- fragmentation or syntactic complexity;
- emotional distance or immediacy;
- figurative-language density;
- humor, irony, or sarcasm;
- internal thought and free indirect discourse;
- meaningful punctuation habits.

### Prose tendencies

Record only source-supported tendencies, such as:

- terse or expansive;
- plain or lexically rich;
- fast or meditative;
- restrained or expressive;
- conversational or formal;
- repetition-heavy or synonym-rich;
- fragmentary or flowing;
- concrete or abstract;
- understated or emphatic.

### Character voices

For recurring characters, record relevant features such as:

- formality;
- vocabulary and education markers;
- age-coded speech when textually supported;
- slang or profanity;
- verbal habits;
- humor;
- sentence length;
- hesitation or confidence;
- recurring forms of address.

The style guide is descriptive, not invented. Never assign a speech trait unsupported by the source.

---

## `glossary.md`

Use `glossary.md` for recurring lexical and continuity decisions that must remain stable, including:

- names and surnames;
- nicknames;
- places;
- organizations;
- titles and forms of address;
- fictional concepts;
- technical/domain terms;
- recurring phrases whose wording matters;
- important ambiguous terms;
- decisions that could otherwise drift between chapters.

Read the glossary before translating a new chapter.

Do not change an established rendering casually. If a better decision is required, update it consistently and record the reason when useful.

---

## Adding a new book

For a real source book:

1. preserve the untouched source under `source/`;
2. determine the source format from the file and its internal structure;
3. extract the real reading order into separate files under `extracted/`;
4. create `metadata.json`, `progress.json`, `glossary.md`, and `style-guide.md`;
5. inspect enough of the original to establish an initial evidence-based style guide before sustained translation;
6. verify chapter order, count, unique slugs, and referenced paths before translating.

Prefer EPUB and structured text formats because their reading order is usually explicit.

For EPUB, use package/navigation/spine structure rather than assuming every XHTML resource is a chapter.

DOCX/PDF may be handled when the active agent can read them reliably. Do not claim automatic support that was not actually used and checked.

---

## Before translating a chapter

Before drafting:

1. read the chapter in full when context limits permit;
2. understand what actually happens in the scene;
3. identify speakers and viewpoint;
4. note emotional state and relationships that affect wording;
5. consider relevant context from preceding chapters;
6. read current glossary decisions;
7. read current style-guide decisions;
8. note difficult, ambiguous, dense, emotional, or plot-critical passages for special review attention.

Do not translate isolated sentences without scene context when chapter context is available.

If a chapter is too large for one working context, it may be processed in technical chunks, but the chapter remains one continuous literary unit. Preserve context across chunk boundaries and ensure the final file does not reveal the technical split.

---

## Chapter workflow

Every chapter must pass through separate translation and review stages.

### Stage 1 — translation draft

Translate the chapter completely.

During this pass:

- translate all source content;
- preserve meaningful paragraphing and formatting;
- do not skip difficult passages;
- do not replace uncertainty with guesswork;
- do not summarize;
- follow the glossary and style guide;
- preserve character distinctions and authorial rhythm.

After the complete translation file exists, the chapter may be marked `translated`.

It must not yet be marked `reviewed`.

### Stage 2 — fidelity review against the original

A chapter cannot become `reviewed` by reading only the target-language text.

Re-open the original and compare sequentially:

- paragraph by paragraph;
- semantic block by semantic block;
- sentence by sentence for difficult, ambiguous, dense, emotional, or plot-critical passages.

For every section, check:

1. Is every source sentence and meaningful fragment represented?
2. Has anything been added that the source does not contain?
3. Are subject and object correct?
4. Are facts correct?
5. Is chronology preserved?
6. Are causal relations preserved rather than invented?
7. Are negations preserved?
8. Is modality preserved?
9. Is certainty or uncertainty preserved?
10. Is emotional intensity equivalent?
11. Has neutral language become more dramatic or sentimental?
12. Has strong language been softened?
13. Is subtext still subtext?
14. Is ambiguity still ambiguous where possible?
15. Are speakers correctly identified?
16. Has a character's motivation been changed or over-explained?
17. Has the narrator's attitude been changed?
18. Are significant repetitions preserved?
19. Is meaningful rhythm or fragmentation preserved?
20. Are there misleading literal calques?
21. Does the passage follow established glossary decisions?
22. Does it match documented style and character voices?
23. Is meaningful formatting preserved?

When a mismatch is found:

1. correct the translation;
2. compare the corrected passage with the source again;
3. continue only after the mismatch is resolved.

### Stage 3 — target-language literary polish

After semantic review, read the chapter as target-language literature.

Correct:

- accidental calques;
- unnatural word order;
- grammar errors;
- unintended bureaucratic or technical diction;
- register mistakes;
- accidental anachronistic wording;
- inconsistent character speech;
- awkwardness introduced by the translator rather than present in the author.

Do not polish away deliberate awkwardness, repetition, restraint, fragmentation, or other source features.

Any substantial wording change made during polish must be checked again against the corresponding original passage.

---

## Definition of `reviewed`

`reviewed` does not mean "the translation reads well."

It means:

> The chapter has been completely translated, systematically compared with the original, corrected for semantic and stylistic drift, checked for completeness, and polished in the target language without departing from the source.

Do not mark a chapter `reviewed` unless this comparison actually occurred.

Before marking `reviewed`, also check for:

- missing paragraphs;
- missing dialogue lines;
- missing internal monologue;
- dropped or merged scene breaks;
- letters, messages, quotations, inscriptions, or embedded documents;
- meaningful italics or emphasis;
- headings and subheadings;
- significant footnotes;
- section separators;
- meaningful blank-space transitions.

Do not use paragraph counts as the sole proof of completeness. Use the source sequence as the primary check.

---

## Dialogue and character voice

Dialogue should be natural in the target language while remaining specific to the speaker.

Preserve meaningful differences in:

- vocabulary;
- formality;
- education;
- age-coded register where supported;
- slang;
- profanity;
- verbal confidence;
- humor;
- verbosity;
- sentence fragments;
- recurring speech habits.

Do not make every speaker equally articulate or stylistically neutral.

---

## Source and chapter rules

- Never modify files in `books/<book-slug>/source/`.
- Extracted originals live only in `extracted/`.
- Translations live only in `translated/`.
- Keep source and translated filenames aligned through `progress.json` paths.
- Do not invent missing chapters.
- If extraction looks wrong, correct extraction and repository state before translating.
- Preserve the real reading order from the source format.
- Do not copy real book content from the canonical public template into a user's workspace.

---

## `progress.json`

`progress.json` is the authoritative resumable work queue.

Top-level fields:

- `schema_version` — currently `1`;
- `book_slug` — stable directory/book identifier;
- `chapters` — ordered chapter records.

Each chapter contains at least:

- `number` — 1-based chapter order;
- `title` — chapter title;
- `slug` — stable chapter identifier;
- `source_path` — extracted original path relative to the book directory;
- `translation_path` — expected translated file path relative to the book directory;
- `status` — `pending`, `extracted`, `translated`, or `reviewed`.

State meanings:

- `pending`: chapter is known but not ready for translation;
- `extracted`: original chapter exists in `extracted/`;
- `translated`: translation file exists but source-comparison review is not complete;
- `reviewed`: translation completed the full review defined here.

Rules:

- Do not mark `extracted` unless `source_path` exists.
- Do not mark `translated` or `reviewed` unless `translation_path` exists.
- Do not silently renumber, drop, or duplicate chapters.
- Update `progress.json` in the same session as the corresponding state change.
- Never use `reviewed` as a convenience marker for "looks finished."

---

## `metadata.json`

Keep metadata deliberately small.

`source_file` is the filename inside the book's `source/` directory, not a repository-absolute path.

Keep `chapter_count` synchronized with `progress.json.chapters`.

---

## Session startup and resume

For an existing book:

1. read this `AGENTS.md` first;
2. read the book's `metadata.json`;
3. read `progress.json`;
4. read `glossary.md`;
5. read `style-guide.md`;
6. inspect existing translations when needed for continuity;
7. verify paths referenced by the next chapter;
8. continue with the first chapter whose status is not `reviewed`, unless the user explicitly requests a different chapter or a quality re-review.

Repository state is more authoritative than previous chat history.

Do not retranslate a reviewed chapter without a concrete reason. If a reviewed chapter must change materially, move it back to an appropriate non-reviewed state until the new review is complete.

---

## Validation

Before declaring a book structurally consistent, verify:

- `books/<book-slug>/source/<metadata.source_file>` exists;
- source files remain unchanged;
- chapter numbers are unique;
- chapter slugs are unique;
- numbering has no unexplained gaps;
- each chapter at `extracted` or later has an existing `source_path`;
- each chapter at `translated` or `reviewed` has an existing `translation_path`;
- `metadata.json.chapter_count` equals the number of chapter records;
- active books have `glossary.md` and `style-guide.md`;
- no translation overwrote an original;
- `reviewed` status is supported by an actual source-comparison review.

If Python is available, use:

```bash
python scripts/book.py validate <book-slug>
```

This is an additional structural check. It is not a substitute for literary review.

---

## Re-reviewing existing translations

When the user questions an existing translation, do not assume `reviewed` means correct.

Perform a fresh source-to-translation comparison.

Pay particular attention to:

- semantic drift;
- invented explanation;
- lost ambiguity;
- changed emotional intensity;
- homogenized dialogue;
- polished-away authorial roughness;
- incorrect pronoun or speaker interpretation;
- omissions;
- wording that is elegant in isolation but unlike the source.

If meaningful problems are found, move affected chapters out of `reviewed`, correct them, and run the complete review again before restoring `reviewed`.

---

## Building output

When requested, assemble translation files in chapter-number order under `output/`.

Markdown is the default transparent format.

Only produce or claim EPUB/DOCX/PDF when the active environment has suitable tooling and the resulting artifact was actually created and checked.

A built artifact does not prove translation quality.

Do not declare a book complete merely because an output file exists.

---

## Completing a book

Before declaring a book complete:

1. verify every intended chapter is present;
2. verify every chapter is `reviewed` under this standard;
3. verify chapter order;
4. verify glossary consistency across the book;
5. verify style-guide consistency and update it with stable late discoveries;
6. re-check selected difficult, emotionally important, ambiguous, and plot-critical passages against the original;
7. verify output ordering and formatting if deliverables were built;
8. confirm the original source remains unchanged.

Do not claim completion while any chapter is unreviewed, missing, or known to contain unresolved fidelity problems.

---

## Privacy, copyright, and repository hygiene

- The canonical public repository is a workflow template, not a book distribution repository.
- Do not publish copyrighted source books unless the user has the right to do so.
- Prefer a private working repository when source or translations should remain private.
- Never commit API keys, credentials, tokens, or unrelated secrets.
- The default workflow does not require an LLM API key.
- Never copy private/example books from another repository into the canonical public template.

---

## Scope discipline

Keep the workflow file-based and inspectable.

Do not add a database, web UI, backend, queue, mandatory LLM API integration, or agent orchestration layer unless a later task explicitly requires it.

Prefer simple, reproducible repository state over hidden process.
