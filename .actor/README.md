# Book Translator — Managed LLM

Upload a book, choose the target language, and receive a reviewed translation.

This Actor uses model credentials managed by the Actor owner. Users do not need to provide an LLM API key.

## Inputs

- one EPUB, HTML/XHTML, Markdown, or TXT file;
- target language;
- optional source-language, title, and author overrides;
- output format: EPUB, Markdown, or both.

## Quality workflow

Each chapter passes through a Translator role and a separate Reviewer role. A chapter is included in the final output only after the current translation has machine-recorded PASS review evidence under the Book Translator workflow.

If the Reviewer requests corrections, the chapter returns to the Translator and is reviewed again.

## Outputs

- OUTPUT_EPUB when EPUB is requested;
- OUTPUT_MARKDOWN when Markdown is requested;
- SUMMARY with chapter count, source-word count, model usage, estimated/measured LLM cost, and output keys;
- per-chapter progress records in the default dataset.

## Status

This branch is an MVP under active validation. Store pricing and very-large-chapter handling are not final yet.
