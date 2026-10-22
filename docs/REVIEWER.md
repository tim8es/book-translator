# Reviewer contract

Read with `AGENTS.md` and the shared `docs/TRANSLATION.md` standard. Compare the source with the accepted translation independently; the Reviewer does not promote a chapter to `reviewed`.

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

## Re-reviewing changed or questioned translations

When an existing translation is questioned, changed materially, or suspected to contain drift, do not rely on an earlier `PASS` as proof that the current artifact is correct.

Perform a fresh source-to-translation comparison for the affected scope. Pay particular attention to semantic drift, invented explanation, lost ambiguity, changed emotional intensity, homogenized dialogue, polished-away roughness, incorrect pronoun or speaker interpretation, omissions, and elegant wording that no longer matches the source.

Return `PASS` only for the artifact actually reviewed. Otherwise return `CORRECTIONS_REQUIRED` and identify what must change.

## Optional structured quality evidence

The hash-bound review ledger proves which exact artifact was reviewed, but
a PASS decision alone does not prove literary quality. For new reviews, prefer
an explicit report with the following compact JSON structure:

```json
{
  "context": "independent_session",
  "checks": {
    "coverage": true,
    "meaning": true,
    "terminology": true,
    "style": true,
    "structure": true
  },
  "findings": [
    {"severity": "minor", "detail": "Awkward repetition in paragraph 4", "resolved": true}
  ]
}
```

Pass it as `review-record ... --quality-report report.json` where CLI execution is
available. The report is stored with the exact source/translation hash-bound
review record, not as a mutable side note. In GitHub-only operation, the same
schema must be validated before recording evidence.

`independent_session` means a separate chat context, not merely switching
role instructions within the translator's existing conversation; use
`bounded_context` for that fallback. PASS is rejected if a structured report
contains unresolved major or critical findings. CORRECTIONS_REQUIRED requires
at least one unresolved finding.

The report is optional to preserve compatibility with historical ledgers.
An absent report must never be represented as independently audited quality.

### Evidence-based Review (v2, opt-in)

For source-linked quality evidence, supply a report with evidence_version=2 to review-record --quality-report.
All five checks (coverage, meaning, terminology, style, structure) remain mandatory.
Every finding provides a stable id, category, severity, detail, resolved flag, source_anchor,
translation_anchor, and resolves (null for first findings). These anchors are verified against
the exact UTF-8 source/translation artifacts before any review ledger write.

Example JSON (invented illustration, NOT a live review):

```json
{
  "evidence_version": 2,
  "context": "independent_session",
  "checks": { "coverage": true, "meaning": true, "terminology": true,
              "style": true, "structure": true },
  "findings": [{
    "id": "modality-1", "category": "modality", "severity": "major",
    "detail": "Possibility was turned into certainty.", "resolved": false,
    "source_anchor": {"quote": "She might have recognized him.", "occurrence": 0},
    "translation_anchor": {"quote": "Она узнала его.", "occurrence": 0},
    "resolves": null
  }]
}
```

Occurrences are zero-based. Repeated quotes require a more specific excerpt or
an explicitly selected nonzero occurrence. Excerpts contain at most 300 characters.
An unresolved omission may use a null translation anchor; resolved issues require a
current translation anchor.

A follow-up to same-source v2 CORRECTIONS_REQUIRED evidence must explicitly recheck
EVERY unresolved prior finding in a new v2 report. Each recheck has its own id and
resolves: {"record_id": "<prior-32-hex-record-id>", "finding_id": "modality-1"}.
To mark a recheck resolved, source/translation anchors must match current artifacts
and the translation SHA-256 must differ from the earlier rejected version.
Otherwise keep the issue unresolved with outcome CORRECTIONS_REQUIRED.

This is citation and correction-lineage verification, not automated literary judgment.
An independent Reviewer must still compare meaning, style and every relevant passage.
Historical v1 evidence remains valid and is not silently upgraded.
