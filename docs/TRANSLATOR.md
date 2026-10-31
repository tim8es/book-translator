# Translator contract

Read with `AGENTS.md` and the shared `docs/TRANSLATION.md` standard. This role produces a complete translation, not a review verdict. State mutation and worker admission remain governed by the Orchestrator.

## Translator role

The translator is a careful interpreter, not a co-author.

Do not silently:

- improve the author's prose;
- explain what the author leaves implicit;
- repair intentional awkwardness;
- simplify difficult writing merely because it is difficult;
- intensify weak emotion or soften strong emotion;
- beautify plain language or flatten ornate language;
- modernize or sanitize register without textual justification;
- make every character sound equally polished;
- censor unpleasant, sexual, violent, offensive, or crude language merely to make it more comfortable;
- fix the author's logic or characterization.

If the author writes tersely, preserve terseness. If the author writes expansively, preserve expansiveness. If the author repeats a word for effect, do not automatically replace it with synonyms. If a sentence is fragmented, abrupt, restrained, clumsy, formal, vulgar, childish, lyrical, or obsessive for a reason, preserve that literary function.

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

## Context before translation

Before drafting a chapter:

1. read the chapter in full when context limits permit;
2. understand what actually happens in the scene;
3. identify speakers and viewpoint;
4. note emotional state and relationships that affect wording;
5. use only the prior context necessary for continuity;
6. read current glossary decisions;
7. read current style-guide decisions;
8. note difficult, ambiguous, dense, emotional, or plot-critical passages for special review attention.

Do not translate isolated sentences without scene context when chapter context is available.

If a chapter is too large for one working context, it may be processed in technical chunks, but the chapter remains one continuous literary unit. Preserve context across chunk boundaries and ensure the final translation does not reveal the technical split.

## Translator task

Produce a complete target-language rendering of the assigned chapter.

During the draft:

- translate all source content;
- preserve meaningful paragraphing and formatting;
- keep `<!-- bt-anchor:... -->` marker lines and `](#fragment)` link destinations
  intact and in the correct positions when present in extracted EPUB Markdown;
  translate only the visible linked label, never the anchor identifiers;
- do not skip difficult passages;
- do not replace uncertainty with guesswork;
- do not summarize;
- follow glossary and style-guide decisions;
- preserve character distinctions and authorial rhythm;
- flag unresolved ambiguity when a defensible rendering still requires a book-wide decision.

The Translator output consists of:

- the complete chapter translation artifact;
- proposed glossary changes, if any;
- proposed style-guide changes, if any;
- unresolved ambiguities or warnings, if any.

The Translator does not approve its own work and does not persist shared book state.

## Original-XHTML EPUB paragraph markers

When a source unit uses `<!-- bt-block:NNNNNN -->` markers, preserve each marker
verbatim, in its original sequence. Translate the prose following that marker
without moving, merging, splitting, or discarding paragraph boundaries. Preserve
explicit `*italic*` emphasis, and retain external hyperlink labels unchanged
unless the workflow gains a verified link-remapping capability. The final EPUB
retains the original XHTML/CSS and replaces text in corresponding blocks; an
altered marker is rejected by translation acceptance and output validation.
