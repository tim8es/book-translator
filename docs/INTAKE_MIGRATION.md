# Recovering historical intake drafts

This adapter supports auditing historical partial translations without manufacturing
review evidence or overwriting existing drafts.

## Preflight (no writes)

Run in an isolated private checkout:

    python scripts/migrate_intake.py the-claiming-moon --chapter-offset 2

The current The Claiming Moon intake identifies 48 original reading-order
units: preface, summary, chapters 1–45, afterword. Offset 2 is specific
to that source, not a generic assumption for all books.

To validate a retrieved original EPUB against the recorded SHA-256:

    python scripts/migrate_intake.py the-claiming-moon --source /private/The_Claiming_Moon.epub --chapter-offset 2

Without a matching original, auditing is permitted but state migration is
blocked. Do not trust a filename, preview EPUB, or old chat as source proof.

## After preflight

1. Create a backup branch and freeze concurrent changes to this book.
2. Initialize the canonical book in an isolated **private** workspace by
   using scripts/book.py extract, scripts/corpus.py seal, validate, verify.
   Preserve all incoming files and the expected pinned workflow SHA.
3. Compare extracted reading order and filenames against the staged draft
   indexes. Resolve ambiguous, partial and conflicting fragments; full indexed Markdown/JSON batches may be reassembled, but plain Markdown titles alone cannot prove completeness.
4. Use --stage with the verified original **only after** canonical metadata,
   source manifest and source revision files have been correctly initialized.
   This writes merely to migration-staging; it never sets translated/reviewed.
5. Orchestrator independently inspects and copies eligible drafts into
   canonical translated paths under real claims, calls accept-translation,
   dispatches a separate source-comparison Reviewer, records a genuine PASS
   or CORRECTIONS_REQUIRED, then calls accept-review on current PASS only.
6. Validate/verify, test resume in a fresh context, and build an EPUB only
   from fully approved canonical book state.

    python scripts/migrate_intake.py the-claiming-moon --source /private/The_Claiming_Moon.epub --chapter-offset 2 --stage

Never commit licensed or confidential source books to a **public** repository.
Normal extraction embeds source bytes; keep resulting private workspace private
or use the documented private-external source mechanism.

IMPORTANT: --stage does not certify literary quality, completeness of plain
Markdown files, or Reviewer PASS. Partial draft stages must be reconciled
outside the production workflow, not relabeled as reviewed.

## Historical block-index conventions

The Claiming Moon source contains empty XHTML paragraph elements. Historical drafts
were produced with two different index systems: all h1-h4/p elements, or only
nonempty h1-h4/p elements. The verifier classifies these *per chapter*, never
assumes a single scheme for the whole book, and fails closed if neither
contiguous index range matches. Matching index counts proves only mechanical
coverage: it does **not** establish text fidelity or Reviewer PASS.
