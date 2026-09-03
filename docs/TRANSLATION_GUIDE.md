# Translation workflow guide

This document describes repository structure and resumable state. It does not replace the translation-quality rules in [`../AGENTS.md`](../AGENTS.md).

If this guide and `AGENTS.md` differ, follow `AGENTS.md` and update this guide.

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

Create this structure only when a real book is added.

## Adding a book

1. Preserve the supplied original under `source/` without modifying it.
2. Determine source format from the file and its internal structure.
3. Extract the real reading order into separate original-language files under `extracted/`.
4. Use stable filenames such as `001-chapter-title.md`.
5. Create `metadata.json`, `progress.json`, `glossary.md`, and `style-guide.md`.
6. Inspect enough of the original to populate an initial evidence-based style guide before sustained translation.
7. Check chapter count, order, slugs, and paths before translation begins.

When Python is available, `scripts/book.py extract` automates the structural portion for supported formats.

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
  "imported_at": "2026-09-03T00:00:00+00:00"
}
```

`source_file` is the filename stored inside `source/`.

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

`progress.json` is the authoritative checkpoint between sessions. Update it whenever a chapter changes state.

## `glossary.md`

Use the glossary for recurring lexical and continuity decisions that must remain stable across chapters.

Recommended structure:

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

At the start of every session:

1. read `AGENTS.md`;
2. read the target book's `metadata.json`;
3. read `progress.json`;
4. read `glossary.md`;
5. read `style-guide.md`;
6. inspect existing translations when needed for continuity;
7. find the first chapter whose status is not `reviewed`;
8. confirm the referenced original chapter exists;
9. continue there unless the user explicitly requests something else.

Repository state is more authoritative than chat history.

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
- active books have `glossary.md` and `style-guide.md`.

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

`scripts/book.py` uses only the Python standard library. It assists with extraction, structural validation, and Markdown assembly.

It does not call an LLM API and does not replace the literary source-comparison review required by `AGENTS.md`.
