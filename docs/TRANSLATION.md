# Literary translation and review contract

This file is authoritative for literary fidelity, Translator behavior, Reviewer source-comparison criteria, and literary review outcomes.

It does not control installation, worker topology, or durable state mutation. `docs/ORCHESTRATION.md` decides when a Reviewer outcome may be persisted as `status=reviewed`.

The `translator` and `reviewer` context profiles in `agent-manifest.json` load this contract together with the global invariants in `AGENTS.md`.

## Non-negotiable translation standard

The translation is not an adaptation, summary, rewrite, localization, or attempt to improve the author.

Preserve, as closely as the target language allows:

- meaning and factual content;
- authorial voice and prose style;
- rhythm and sentence movement;
- tone and emotional intensity;
- narrative distance and atmosphere;
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

## Semantic fidelity

Preserve every meaningful element of the source.

Pay special attention to:

- who performs and receives an action;
- chronology;
- tense and aspect where meaningful;
- negation and conditions;
- degree of certainty;
- possibility versus fact;
- intention versus action;
- perception versus knowledge;
- comparison;
- spatial, temporal, and causal relations;
- pronoun reference;
- emotional force;
- whether a statement belongs to the narrator, a character, or free indirect discourse.

Do not collapse distinctions such as `maybe`, `probably`, `apparently`, `certainly`, `she thought`, `she knew`, and `she wanted to believe` when the source distinguishes them.

Do not replace a precise meaning with a merely adjacent meaning when a closer rendering is available.

## Ambiguity, uncertainty, and subtext

Preserve uncertainty when the source is uncertain.

If the original deliberately permits multiple readings, preserve that openness whenever reasonably possible. Do not force one interpretation merely because it seems likely.

Do not turn subtext into explanation. If the author makes the reader infer something, the translation should normally require the same inference.

When serious ambiguity cannot be preserved directly, choose the least interpretive rendering that fits the scene and propose a glossary or style-guide decision when it is likely to recur or affect later passages.

## Natural target-language prose

Fidelity does not mean word-for-word literalism.

A literal rendering is wrong when it preserves words but loses meaning, tone, rhythm, idiom, or literary function.

You may restructure syntax, change word order, use functional equivalents for idioms, change grammatical construction where required, and split or combine clauses when necessary for faithful target-language syntax.

Such changes are acceptable only when the result preserves the source's meaning, force, register, implication, and rhythm as closely as possible.

## Literary memory: style guide

Use `style-guide.md` as durable evidence-based memory of the source's literary behavior. It is descriptive, not invented.

Before sustained translation, inspect enough of the source to identify supported tendencies. During later chapters, propose updates only when new evidence is stable enough to matter across the book.

At minimum consider:

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

Record source-supported tendencies such as terse/expansive, plain/lexically rich, fast/meditative, restrained/expressive, conversational/formal, repetition-heavy/synonym-rich, fragmentary/flowing, concrete/abstract, and understated/emphatic.

### Character voices

For recurring characters, record supported differences in formality, vocabulary, education markers, age-coded register, slang/profanity, verbal habits, humor, sentence length, hesitation/confidence, and forms of address.

Do not invent psychological or social traits merely to make the guide more detailed.

## Literary memory: glossary

Use `glossary.md` for recurring lexical and continuity decisions that must remain stable, including:

- names and surnames;
- nicknames;
- places and organizations;
- titles and forms of address;
- fictional concepts;
- technical or domain terms;
- recurring phrases whose wording matters;
- important ambiguous terms;
- decisions that could otherwise drift between chapters.

Read the current glossary before translating or reviewing a chapter. Do not change an established rendering casually. If evidence justifies a better decision, propose the change and explain enough for the Orchestrator to apply it consistently.

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

## Reviewer role

The Reviewer independently evaluates the source and the current translation. Review the artifact, not the Translator's hidden reasoning or justification.

A fluent target-language text is not sufficient evidence of fidelity. The source must be re-opened and compared with the translation.

## Source-comparison review

Compare the source and translation sequentially:

- paragraph by paragraph;
- semantic block by semantic block;
- sentence by sentence for difficult, ambiguous, dense, emotional, or plot-critical passages.

For every section, check:

1. Is every source sentence and meaningful fragment represented?
2. Has anything been added that the source does not contain?
3. Are subject, object, facts, quantities, and references correct?
4. Is chronology preserved?
5. Are causal relations preserved rather than invented?
6. Are negation, conditions, and modality preserved?
7. Is certainty or uncertainty preserved?
8. Is emotional intensity equivalent?
9. Has neutral language become more dramatic, sentimental, comic, or judgmental?
10. Has strong, crude, offensive, sexual, violent, or otherwise marked language been softened without source support?
11. Is subtext still subtext rather than explanation?
12. Is ambiguity still ambiguous where reasonably possible?
13. Are speakers, pronoun references, viewpoint, and free indirect discourse correctly identified?
14. Has motivation been changed, simplified, or over-explained?
15. Has the narrator's or character's attitude changed?
16. Are significant repetitions preserved rather than automatically varied?
17. Is meaningful rhythm, pacing, fragmentation, or sentence movement preserved?
18. Are there misleading literal calques or false friends?
19. Does the passage follow established glossary and style decisions?
20. Is meaningful formatting preserved?

Also check for omissions or corruption of:

- dialogue;
- internal monologue;
- scene breaks;
- embedded letters, messages, quotations, or documents;
- headings and subheadings;
- meaningful emphasis;
- significant footnotes or endnotes when part of the intended text;
- section separators;
- meaningful blank-space transitions.

Do not use paragraph counts as the sole proof of completeness. Use the source sequence as the primary check.

When a mismatch is found, identify the concrete source/translation discrepancy and provide or request a correction. Any corrected passage must be checked against the corresponding source again before a PASS outcome.

## Target-language literary polish

After semantic source-comparison, read the chapter as target-language literature.

Correct accidental calques, unnatural word order, grammar errors, unintended bureaucratic or technical diction, register mistakes, accidental anachronisms, inconsistent character speech, and translator-created awkwardness.

Do not polish away deliberate awkwardness, repetition, restraint, fragmentation, roughness, simplicity, verbosity, or other source features.

Any substantial wording change during polish must be checked again against the corresponding original passage.

## Reviewer outcome interface

The Reviewer returns exactly one outcome category:

- `PASS` — the current translation satisfies the source-comparison and literary standards in this contract; optionally include proposed glossary/style decisions or non-blocking notes.
- `CORRECTIONS_REQUIRED` — concrete fidelity or literary problems remain; return actionable findings and corrections or passages that must be corrected and re-reviewed.

`PASS` is a literary review result, not a repository state mutation.

PASS alone does not mutate `progress.json`. The Orchestrator applies any durable state transition under `docs/ORCHESTRATION.md`.

If the Reviewer returns `CORRECTIONS_REQUIRED`, the corrected translation must be reviewed again until a Reviewer can return `PASS` for the corrected artifact.

## Literary meaning of PASS

`PASS` does not mean "the translation reads well."

It means:

> The assigned chapter has been completely translated, systematically compared with the original, corrected for semantic and stylistic drift, checked for completeness, and polished in the target language without departing from the source.

A Reviewer must not return `PASS` unless that source-comparison actually occurred.

## Dialogue and character voice

Dialogue should be natural in the target language while remaining specific to the speaker.

Preserve meaningful differences in vocabulary, formality, education, age-coded register where supported, slang, profanity, verbal confidence, humor, verbosity, fragments, hesitation, and recurring speech habits.

Do not make every speaker equally articulate, polished, neutral, modern, or similar to the narrator.

## Re-reviewing changed or questioned translations

When an existing translation is questioned, changed materially, or suspected to contain drift, do not rely on an earlier `PASS` as proof that the current artifact is correct.

Perform a fresh source-to-translation comparison for the affected scope. Pay particular attention to semantic drift, invented explanation, lost ambiguity, changed emotional intensity, homogenized dialogue, polished-away roughness, incorrect pronoun or speaker interpretation, omissions, and elegant wording that no longer matches the source.

Return `PASS` only for the artifact actually reviewed. Otherwise return `CORRECTIONS_REQUIRED` and identify what must change.
