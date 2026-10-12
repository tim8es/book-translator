## AI Book Translator with independent review

**Translate an EPUB, TXT, Markdown, or HTML book into another language and download the result as EPUB, Markdown, or both.** Upload one file, choose the destination language, and run. No personal LLM API key is needed: the Actor operator manages the model connection.

The workflow translates one chapter at a time, has a separate AI reviewer check each translation, and requests correction if the reviewer finds problems. Only chapters with current, recorded **PASS** review evidence are included in the final assembled book. This is automated review, **not a guarantee of human-editor quality**.

### Who can use it?

- Readers translating documents they own or have permission to process.
- Publishers evaluating machine-assisted translation before human editing.
- Researchers preparing working translations of permitted materials.
- Developers who need a repeatable translation workflow through the Apify API.

Please upload only content that you have the rights and authorization to translate and transmit to an AI service.

## How to translate a book

1. Upload **one** EPUB, TXT, Markdown, HTML, or XHTML file in **Book file**.
2. Set **Target language**, for example `Russian`, `German`, or `English`. Optionally specify the source language and book metadata.
3. Choose **EPUB**, **Markdown**, or **EPUB + Markdown** as the output.
4. Run the Actor. Check the dataset for chapter progress and the output tab for the finished files.
5. Download `OUTPUT_EPUB` and/or `OUTPUT_MARKDOWN` from the run's key-value store.

No manual prompting or external API key is required. You can also start runs through the Apify API or integrate them with other automations.

## Supported file formats and limitations

| Input | Supported |
| --- | --- |
| EPUB | Yes |
| TXT | Yes |
| Markdown (`.md`, `.markdown`) | Yes |
| HTML / XHTML | Yes |
| PDF / DOCX | Not yet |

- One file per run. The Actor does not combine multiple books.
- Each source chapter must fit within the operator's context limit. **Very long chapters may be rejected rather than split**; this release does not yet implement chunking.
- No cross-run resume: if the run fails, successfully reviewed chapters may be available in the same run's storage, but a fresh run starts again.
- Both translation and quality checking use AI. Names, dialogue, consistency, layout, and meaning can contain errors; verify important or publication-bound work with a human.
- Format handling is intentionally conservative; complex EPUB layouts and images may not be reproduced faithfully.
- A job may end in failure rather than return a partial, unreviewed translation.

## Input example

Use the upload control to produce a file URL automatically. The sample below illustrates the API input **after** upload; replace the URL with the actual uploaded file location.

```json
{
  "bookFiles": ["https://example.com/my-permitted-book.epub"],
  "targetLanguage": "Russian",
  "sourceLanguage": "English",
  "title": "My Book",
  "author": "Example Author",
  "outputFormat": "both"
}
```

## Output and recovery

A successful run stores these entries in its default key-value store:

| Record | Description |
| --- | --- |
| `OUTPUT_EPUB` | Complete, reviewed EPUB, if selected |
| `OUTPUT_MARKDOWN` | Complete, reviewed Markdown, if selected |
| `CHAPTER_000001`, etc. | Individually reviewed chapter recovery files |
| `SUMMARY` | Chapter count, source words, completion status, output keys, and owner-side model usage/cost |

The default dataset contains a row per completed chapter and a final summary. A failure produces a failure summary with the last chapter reached and completed-chapter IDs when possible.

**Illustrative dataset row** (not a claim about a live run):

```json
{
  "chapter": 1,
  "words": 1200,
  "chargedUnits": 2,
  "reviewRounds": 1,
  "status": "reviewed",
  "outputKey": "CHAPTER_000001"
}
```

The `chargedUnits` property indicates the number of 1,000-source-word units assigned to the chapter; it is **not** a dollar amount, and private test runs may skip charging entirely.

## How much does the Actor cost?

The planned public billing model is **pay per event**:

- **Book start:** one `book-started` event after validation and budget checks.
- **Reviewed translation:** one `translation-1k-words` event for each started 1,000-source-word unit of a *successfully reviewed and saved* chapter. Rounding happens separately for each chapter.

For a book with two chapters of 1,200 words and 400 words, the workflow would use **one start event and three word-unit events**. The dollar price per event is displayed by Apify Store when monetization is configured; **no fixed price is promised in this documentation**. Set the maximum run charge high enough to cover the entire book; the Actor checks the price capacity before starting paid model work.

If a chapter fails review, the corresponding reviewed-chapter event is not charged. The start event, already completed chapters, and any platform usage may still be billable according to Apify's pricing settings. Avoid running lengthy books until you have checked the live per-event prices.

## Privacy and data retention

Your uploaded text is processed inside the Apify run and sent to the operator-configured external LLM service for translation and review. Finished chapters, books, and status metadata are stored in Apify run storage and are subject to its access controls and retention settings. **Do not upload private, confidential, or regulated materials unless you have assessed these processing arrangements.** The Actor does not require users to supply their own LLM API credentials.

## Troubleshooting and FAQ

**Why did my book fail before translation?** Check for an unsupported format, missing upload URL, source size or context-limit errors, or a run budget below the required event price. The `SUMMARY` record and run logs provide a short diagnosis.

**Can I translate a PDF?** No. Convert it to EPUB, Markdown, HTML, or TXT first and check the converted text for omissions.

**Where are the files?** Open the run's output, follow the translation-files link to its key-value store, and download the `OUTPUT_*` entries.

**Can I continue after a failure?** You can retrieve already reviewed chapters saved in the failed run; automated cross-run continuation is not yet supported.

**Will the translation be ready to publish without proofreading?** Not necessarily. Independent AI review reduces some errors but cannot replace a professional human review for high-stakes or publication-quality work.

For reproducibility and issue reports, see the [Book Translator project](https://github.com/tim8es/book-translator). Include the run ID and an error description; never include an API key or private book content in a public issue.
