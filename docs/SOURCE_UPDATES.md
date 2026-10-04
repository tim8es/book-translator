# Source Edition Updates

This contract governs later editions of a book source after the book workspace already exists.

It is an Orchestrator contract. Translator and Reviewer workers do not mutate source revision state.

## 1. Durable source revision model

Every current-workflow book has:

- `source-revisions.json`: the authoritative source-edition catalog;
- exactly one active source revision;
- immutable revision snapshots under `source-revisions/<source-revision>/`;
- `metadata.json.source.revision_id` equal to the active revision;
- stable `unit_id` values in `progress.json`.

A source revision is an edition of the source corpus, not a workflow revision. Do not confuse `source-000002` with `metadata.json.workflow.resolved_revision`.

The initial extracted corpus is `source-000001`. Later sources allocate monotonically increasing source revision IDs. Stable unit IDs are also allocated monotonically and are never reused after deletion or discard.

## 2. Source immutability

Never overwrite an existing source binary, extracted artifact, translation, review record, or revision snapshot to make a later source edition fit the current state.

A later source with a different SHA-256 must go through:

```text
candidate source
  -> extract complete candidate corpus
  -> compare against active revision
  -> stage immutable revision
  -> classify delta
  -> reuse only hash-identical units
  -> explicitly invalidate changed/new units
  -> safety gate destructive changes
  -> promote revision
```

Promoted embedded source binaries use revision-qualified filenames. Historical source and extracted artifacts remain available as provenance.

## 3. Stable unit identity

`number` is current reading order. It is not identity.

`unit_id` is durable identity.

Inserting a new unit between existing units renumbers reading order without changing the `unit_id` of hash-identical existing units. Claims, Translator acceptance, Reviewer evidence, and output provenance bind to `unit_id` plus artifact hashes.

This prevents a newly inserted chapter from invalidating unrelated later chapters merely because their displayed numbers shifted.

## 4. Delta classification

Compare the complete candidate corpus against the complete active corpus before any promotion.

Classify every candidate unit as one of:

- `unchanged`: exact extracted-byte SHA-256 match in sequence; preserve stable unit ID, source path, translation path, lifecycle state, Translator acceptance, and current hash-bound review evidence;
- `changed`: confidently matched to an existing unit but source bytes differ, or the unit moved in a way that cannot safely preserve sequence evidence; preserve stable unit ID but allocate revision-qualified source/translation paths and reset lifecycle to `extracted`;
- `new`: no safe prior-unit match; allocate a new stable unit ID and start at `extracted`;
- `deleted`: an active unit absent from the candidate edition.

Do not infer reuse from chapter number alone.

Hash-identical sequential reuse is the primary automatic rule. A remaining hash-identical unit may also be reused across reordering only when that hash maps uniquely on both sides. Unique normalized titles may identify a changed unit after the exact-match pass. Ambiguous matches must not silently reuse translations or reviews.

## 5. Automatic reuse boundary

Reuse is allowed only when all of the following are true:

1. source bytes are hash-identical;
2. the stable unit match is deterministic;
3. the prior translation artifact still matches its Translator acceptance SHA-256;
4. current review evidence, when lifecycle is `reviewed`, still resolves against the same source SHA-256, translation SHA-256, workflow revision, and review contract revision.

Changed or new units must never inherit `translation_acceptance` or a `reviewed` lifecycle state.

The append-only review ledger may retain historical evidence. Stale historical evidence is provenance, not current acceptance.

## 6. Auxiliary units

Frontmatter, title/summary pages, prologues, epilogues, author notes, and afterwords are source units when they appear in the extracted reading order.

They follow the same identity, hashing, delta, translation, review, and output rules as numbered narrative chapters. Do not silently drop or regenerate auxiliary text during EPUB assembly.

## 7. Staging and promotion

Use:

```bash
python scripts/book.py update-source <book-slug> <new-source> --json
```

Safe additive/change deltas may promote automatically after staging.

A delta containing deletions must remain staged and return `staged_requires_decision` unless deletion approval is explicit:

```bash
python scripts/book.py promote-source-update <book-slug> <source-revision> --allow-deletions --json
```

To inspect history:

```bash
python scripts/book.py source-revisions <book-slug> --json
python scripts/book.py source-revisions <book-slug> --verify --json
```

To retain evidence but reject a candidate:

```bash
python scripts/book.py discard-source-update <book-slug> <source-revision> --json
```

Use `--stage-only` when the user or workflow explicitly requires inspection before any promotion.

The source-storage mode inherits from the active book unless explicitly changed with `--private-source` or `--embedded-source`.

## 8. Crash-safe promotion

Promotion is a coordinated state transition.

Before promotion:

- current corpus validation must pass;
- no literary claim may be active;
- finalization must not be active.

Promotion writes a durable `.workflow/source-promotion.json` marker containing base state revisions and target hashes. While that marker exists, claim admission, Translator acceptance, proposal reconciliation, finalization, validation-dependent output, and another source promotion must fail closed except for recovery of the same promotion.

Promotion is additive for artifacts and compare-and-swap for mutable state. On retry:

- a target document already equal to the recorded target is accepted;
- a document still at the recorded base revision may advance;
- any unrelated revision is a conflict.

Do not delete the recovery marker until target metadata, progress, source manifest, source revision catalog, and required artifacts all pass read-back validation.

## 9. Persistence invariant

A lifecycle claim is valid only when the durable artifacts that justify it exist.

For every `translated` or `reviewed` unit:

- source artifact exists and matches current source identity;
- translation artifact exists;
- current `translation_acceptance` binds exact source and translation SHA-256;
- `reviewed` additionally resolves to current PASS review evidence.

A session must not report successful translation/review if the corresponding durable artifact/evidence write failed.

## 10. Output invariant

EPUB/Markdown assembly is a pure projection of the active durable state.

Do not translate, reconstruct missing chapters, or search legacy repositories during `build`.

If required current artifacts are missing, build fails and the book state must be repaired first.

Generated output identity includes the active source revision and source SHA-256. A source promotion therefore makes prior generated output stale even when all translated text is reusable.

No book-specific export workflow is required. Use the canonical builder:

```bash
python scripts/book.py build <book-slug> --format epub
python scripts/book.py build-status <book-slug> --format epub --json
```

Final EPUB requires current reviewed state and PASS evidence. `--allow-unreviewed` is preview-only.

## 11. Existing books

Do not silently switch an existing book to a newer workflow revision.

After the user explicitly upgrades a book to a workflow revision that includes this contract, initialize `source-revisions.json` from the validated current corpus once. The source-update command can perform this bootstrap when the upgraded workspace has a valid source manifest and no active claims.

A legacy repository is not an operational fallback after migration. Required source, translation, glossary, style, review, and output state must live in the book's authoritative private workspace.

## 12. Orchestrator decision rule

When the user supplies a newer edition of an existing book:

1. restore the book from durable private state;
2. honor the book-pinned workflow revision;
3. if that revision supports source revisions, run complete candidate delta analysis;
4. reuse only proven unchanged units;
5. translate/review only changed or new units;
6. promote only after safety gates pass;
7. build output only from the promoted durable state.

Do not ask the user to choose technical filenames, revision IDs, directory layouts, or reuse mechanics.
