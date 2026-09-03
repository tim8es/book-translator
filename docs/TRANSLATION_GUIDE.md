# Translation workflow guide

This document describes repository structure and resumable book state. It does not replace the literary-quality rules in [`../AGENTS.md`](../AGENTS.md) or execution topology in [`ORCHESTRATION.md`](ORCHESTRATION.md).

`agent-manifest.json.contract_read_order` is the canonical instruction-loading order. This guide must not define a competing bootstrap sequence.

## Per-book layout

```text
books/<book-slug>/
├── source/
├── extracted/
├── translated/
├── output/
├── metadata.json
├── progress.json
├── glossary.md
└── style-guide.md
```

For a namespaced integration, the same layout is relative to `.book-translator/`.

Create a book workspace only when a real book is added.

## Adding a book

1. Preserve the supplied original under `source/` without modifying it.
2. Determine source format from the file and its internal structure.
3. Extract the real reading order into separate original-language files under `extracted/`.
4. Use stable filenames such as `001-chapter-title.md`.
5. Create `metadata.json`, `progress.json`, `glossary.md`, and `style-guide.md`.
6. Copy the active workflow repository/ref/revision into `metadata.json.workflow`.
7. Inspect enough of the original to populate an initial evidence-based style guide before sustained translation.
8. Check chapter count, order, slugs, and paths before translation begins.

When Python is available, `scripts/book.py extract` automates the structural portion for supported formats and copies provenance from `.book-translator-install.json` when that file exists.

## Format guidance

- **EPUB** — preferred. Use package/spine reading order; do not treat every XHTML resource as a chapter blindly.
- **HTML/XHTML** — use headings, sections, and document order.
- **Markdown/TXT** — use explicit chapter/part headings. When boundaries are ambiguous, keep a larger unit instead of inventing chapters.
- **DOCX** — use document headings/sections when the active agent can read the file reliably.
- **PDF** — may require additional extraction work. Preserve order and verify chapter boundaries; OCR/layout reconstruction is not assumed.

The per-book repository structure is independent of source format.

## `metadata.json`

Example:

```json
{
  "schema_version": 1,
  "title": "Book title",
  "author": "Author name",
  "source_language": "en",
  "target_language": "ru",
  "source_format": "epub",
  "source_file": "book.epub",
  "chapter_count": 12,
  "imported_at": "2026-09-03T00:00:00+00:00",
  "workflow": {
    "repository": "https://github.com/tim8es/book-translator",
    "requested_ref": "main",
    "resolved_revision": "<commit-sha>"
  }
}
```

`source_file` is the filename stored inside `source/`.

`workflow` records the contract revision associated with this specific book. It is intentionally per-book because one workspace may contain books initialized under different Book Translator revisions.

- `workflow.repository` — canonical workflow repository used for the book.
- `workflow.requested_ref` — branch/tag/commit requested when the book was initialized.
- `workflow.resolved_revision` — concrete workflow commit SHA when resolution was possible; otherwise `null`.

Do not silently replace a book's `resolved_revision` merely because the currently installed workflow is newer. A workflow upgrade is an explicit state transition followed by compatibility/validation checks.

Legacy workspaces without `workflow` may still be readable, but exact workflow reproducibility is unavailable until provenance is established.

`author` may be `null`; an unknown source language may be `"unknown"`.

## `progress.json`

Example:

```json
{
  "schema_version": 1,
  "book_slug": "book-slug",
  "chapters": [
    {
      "number": 1,
      "title": "Chapter One",
      "slug": "chapter-one",
      "source_path": "extracted/001-chapter-one.md",
      "translation_path": "translated/001-chapter-one.md",
      "status": "extracted"
    }
  ]
}
```

Allowed statuses:

| Status | Meaning |
| --- | --- |
| `pending` | Chapter is known but extraction is not complete. |
| `extracted` | Original chapter file exists and is ready for translation. |
| `translated` | Translation exists but source-comparison review is incomplete. |
| `reviewed` | Translation completed the full review required by `AGENTS.md`. |

`progress.json` is the authoritative chapter work queue. Update it whenever a chapter changes state.

## `glossary.md`

Use the glossary for recurring lexical and continuity decisions that must remain stable across chapters.

```markdown
# Glossary

| Original | Translation | Type | Notes |
| --- | --- | --- | --- |
| John Smith | Джон Смит | person | Keep this spelling throughout |
```

Useful `Type` values include `person`, `place`, `organization`, `term`, `nickname`, `title`, `recurring phrase`, and `other`.

## `style-guide.md`

Use the style guide to record evidence-based observations about the book's literary voice and recurring stylistic decisions.

Typical areas include:

- narrator and point of view;
- narrative distance;
- register;
- sentence rhythm and complexity;
- emotional restraint or intensity;
- figurative-language density;
- humor and irony;
- internal thought;
- recurring character voices;
- meaningful repetition;
- recurring ambiguities that should remain unresolved in translation.

The style guide describes the source; it must not invent traits for the author or characters.

## Resuming translation

Repository state is more authoritative than chat history.

At resume time:

1. identify the Book Translator `install_root` and target book;
2. read the book's `metadata.json.workflow` provenance;
3. load the Book Translator contract for that book's recorded `resolved_revision` (or most specific recorded ref) using `agent-manifest.json.contract_read_order`;
4. load [`docs/ORCHESTRATION.md`](ORCHESTRATION.md) from that same workflow revision;
5. read `progress.json`, `glossary.md`, and `style-guide.md`;
6. inspect only the existing source/translation context needed for continuity;
7. find the first chapter whose status is not `reviewed`;
8. confirm the referenced original chapter exists;
9. continue there unless the user explicitly requests another scope.

If `<install_root>/.book-translator-install.json` describes a different workflow revision from `metadata.json.workflow`, do not silently upgrade the book. The book-level provenance controls its resume contract unless an explicit upgrade is performed.

If exactly one incomplete book exists and the user has not named another, it may be selected automatically. If multiple incomplete books exist and user intent does not identify one, ask only which book to resume.

## Chapter state flow

```text
pending -> extracted -> translated -> reviewed
```

The critical distinction is between the last two states:

- `translated` means a complete target-language file exists;
- `reviewed` means it was systematically compared with the original for meaning, omissions, additions, tone, ambiguity, voice, rhythm, and meaningful formatting, then polished without introducing new drift.

## Structural validation

A book is structurally consistent when:

- `source/<metadata.source_file>` exists;
- the original source was not overwritten;
- chapter numbers are unique and ordered;
- chapter slugs are unique;
- there are no unexplained number gaps;
- all chapters at `extracted`, `translated`, or `reviewed` have an existing `source_path`;
- all chapters at `translated` or `reviewed` have an existing `translation_path`;
- `chapter_count` equals the number of progress entries;
- translations are stored under `translated/`;
- active books have `glossary.md` and `style-guide.md`;
- new books have a `workflow` object in `metadata.json`;
- when `resolved_revision` is unavailable, validation reports that exact workflow reproducibility cannot be guaranteed.

When Python is available:

```bash
python scripts/book.py validate <book-slug>
```

Structural validation is separate from literary review. A structurally valid book may still contain an inaccurate translation.

## Building output

When requested, combine translation files in chapter-number order and write the result under `output/`.

Markdown is the default transparent format.

When Python is available:

```bash
python scripts/book.py build <book-slug>
```

EPUB/DOCX/PDF reconstruction should only be claimed when the active environment actually produced and checked the artifact.

An output artifact is not proof that every chapter satisfies the `reviewed` standard.

## Optional helper

`scripts/book.py` uses only the Python standard library. It assists with extraction, structural validation, provenance capture, and Markdown assembly.

It does not call an LLM API and does not replace the literary source-comparison review required by `AGENTS.md`.
