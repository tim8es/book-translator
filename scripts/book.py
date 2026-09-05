#!/usr/bin/env python3
"""File-based helper for Book Translator.

The helper performs structural work only: source extraction, workspace validation,
and Markdown assembly. It does not call an LLM and does not perform literary review.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from workflow_v2 import (
    FilesystemStorage,
    RepositoryError,
    ReviewEvidenceError,
    ReviewLedgerManager,
    SchemaError,
    SchemaKind,
    StorageError,
    WorkflowStateRepository,
)
from workflow_v2.claim_cli import ClaimCliError, register_claim_commands
from workflow_v2.reviews import REVIEW_EVIDENCE_VERSION
from workflow_v2.schemas import SCHEMA_VERSION


ALLOWED_STATUSES = {"pending", "extracted", "translated", "reviewed"}
CANONICAL_REPOSITORY = "https://github.com/tim8es/book-translator"
REPO_ROOT = Path(__file__).resolve().parents[1]


class BookError(RuntimeError):
    """Expected workflow error suitable for CLI output."""


@dataclass
class Chapter:
    title: str
    content: str


class BlockHTMLParser(HTMLParser):
    """Extract headings and readable block text from simple HTML/XHTML."""

    BLOCK_TAGS = {"p", "div", "li", "blockquote", "pre"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP_TAGS = {"script", "style", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._tag: str | None = None
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.HEADING_TAGS or tag in self.BLOCK_TAGS:
            self._flush()
            self._tag = tag
        elif tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._tag == tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if not self._parts:
            self._tag = None
            return
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._parts))
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        if text:
            self.blocks.append((self._tag or "text", text))
        self._parts = []
        self._tag = None


def repo_root() -> Path:
    return REPO_ROOT


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "book"


def decode_text(data: bytes, source: str) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise BookError(f"Cannot decode text from {source}; convert it to UTF-8 first.")


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        if not zipfile.is_zipfile(path):
            raise BookError(f"{path} has .epub extension but is not a ZIP-based EPUB.")
        with zipfile.ZipFile(path) as zf:
            if "META-INF/container.xml" not in set(zf.namelist()):
                raise BookError(f"{path} does not contain META-INF/container.xml.")
        return "epub"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".xhtml":
        return "xhtml"
    if suffix == ".txt":
        return "txt"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".docx":
        raise BookError("DOCX automatic extraction is not implemented by this helper; use an agent capable of reading DOCX.")
    if suffix == ".pdf":
        raise BookError("PDF automatic extraction is not implemented by this helper; use an agent capable of reliable PDF extraction.")
    raise BookError(f"Unsupported source format: {suffix or '<no extension>'}")


def html_blocks(text: str) -> list[tuple[str, str]]:
    parser = BlockHTMLParser()
    parser.feed(text)
    parser.close()
    return parser.blocks


def blocks_to_markdown(blocks: Iterable[tuple[str, str]]) -> str:
    lines: list[str] = []
    for tag, text in blocks:
        if tag in BlockHTMLParser.HEADING_TAGS:
            lines.append(f"{'#' * int(tag[1])} {text}")
        elif tag == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines).strip() + "\n"


def split_html_document(text: str, fallback_title: str) -> list[Chapter]:
    blocks = html_blocks(text)
    if not blocks:
        raise BookError(f"No readable text found in {fallback_title}.")

    split_level = next(
        (
            level
            for level in ("h1", "h2")
            if sum(1 for tag, _ in blocks if tag == level) >= 2
        ),
        None,
    )
    if split_level is None:
        title = next((value for tag, value in blocks if tag in BlockHTMLParser.HEADING_TAGS), fallback_title)
        return [Chapter(title=title, content=blocks_to_markdown(blocks))]

    chapters: list[Chapter] = []
    current: list[tuple[str, str]] = []
    current_title = fallback_title
    for tag, value in blocks:
        if tag == split_level:
            if current:
                chapters.append(Chapter(current_title, blocks_to_markdown(current)))
                current = []
            current_title = value
        current.append((tag, value))
    if current:
        chapters.append(Chapter(current_title, blocks_to_markdown(current)))
    return [chapter for chapter in chapters if chapter.content.strip()]


def split_markdown(text: str, fallback_title: str) -> list[Chapter]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,2})\s+(.+?)\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    split_level = next(
        (level for level in (1, 2) if sum(1 for _, found, _ in headings if found == level) >= 2),
        None,
    )
    if split_level is None:
        return [Chapter(fallback_title, text.strip() + "\n")]

    starts = [(index, title) for index, level, title in headings if level == split_level]
    chapters: list[Chapter] = []
    for item_index, (start, title) in enumerate(starts):
        end = starts[item_index + 1][0] if item_index + 1 < len(starts) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        if content:
            chapters.append(Chapter(title, content + "\n"))
    return chapters


TXT_CHAPTER_RE = re.compile(
    r"^\s*(?:(?:chapter|глава)\s+(?:\d+|[ivxlcdm]+)|(?:часть|part)\s+(?:\d+|[ivxlcdm]+))\b.*$",
    flags=re.IGNORECASE,
)


def split_txt(text: str, fallback_title: str) -> list[Chapter]:
    lines = text.splitlines()
    starts = [(index, line.strip()) for index, line in enumerate(lines) if TXT_CHAPTER_RE.match(line)]
    if len(starts) < 2:
        return [Chapter(fallback_title, text.strip() + "\n")]

    chapters: list[Chapter] = []
    for item_index, (start, title) in enumerate(starts):
        end = starts[item_index + 1][0] if item_index + 1 < len(starts) else len(lines)
        content = "\n".join(lines[start:end]).strip()
        if content:
            chapters.append(Chapter(title, content + "\n"))
    return chapters


def read_epub(source: Path) -> tuple[list[Chapter], dict[str, str | None]]:
    with zipfile.ZipFile(source) as zf:
        try:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
        except (KeyError, ET.ParseError) as exc:
            raise BookError(f"Invalid EPUB container: {exc}") from exc

        rootfile = next(
            (node.attrib.get("full-path") for node in container.iter() if node.tag.endswith("rootfile")),
            None,
        )
        if not rootfile:
            raise BookError("EPUB container.xml has no rootfile.")

        try:
            opf = ET.fromstring(zf.read(rootfile))
        except (KeyError, ET.ParseError) as exc:
            raise BookError(f"Invalid EPUB package document: {exc}") from exc

        def metadata_text(local_name: str) -> str | None:
            for node in opf.iter():
                if node.tag.endswith(local_name) and node.text and node.text.strip():
                    return node.text.strip()
            return None

        metadata = {
            "title": metadata_text("title"),
            "author": metadata_text("creator"),
            "source_language": metadata_text("language"),
        }

        manifest: dict[str, tuple[str, str, str]] = {}
        for node in opf.iter():
            if not node.tag.endswith("item"):
                continue
            item_id = node.attrib.get("id")
            href = node.attrib.get("href")
            if item_id and href:
                manifest[item_id] = (
                    href,
                    node.attrib.get("media-type", ""),
                    node.attrib.get("properties", ""),
                )

        spine_ids = [
            node.attrib["idref"]
            for node in opf.iter()
            if node.tag.endswith("itemref") and node.attrib.get("idref")
        ]
        opf_dir = Path(rootfile).parent

        chapters: list[Chapter] = []
        for idref in spine_ids:
            item = manifest.get(idref)
            if not item:
                continue
            href, media_type, properties = item
            if "nav" in properties.split() or media_type not in {"application/xhtml+xml", "text/html"}:
                continue
            epub_path = (opf_dir / unquote(href.split("#", 1)[0])).as_posix()
            try:
                raw = zf.read(epub_path)
            except KeyError as exc:
                raise BookError(f"EPUB spine references missing file: {epub_path}") from exc
            text = decode_text(raw, epub_path)
            fallback = Path(epub_path).stem.replace("_", " ").replace("-", " ").strip() or idref
            try:
                chapters.extend(split_html_document(text, fallback))
            except BookError:
                continue

        if not chapters:
            raise BookError("No readable XHTML/HTML chapters found in EPUB spine.")
        return chapters, metadata


def extract_chapters(source: Path, source_format: str) -> tuple[list[Chapter], dict[str, str | None]]:
    if source_format == "epub":
        return read_epub(source)

    text = decode_text(source.read_bytes(), str(source))
    fallback = source.stem.replace("_", " ").replace("-", " ").strip() or "Book"
    if source_format in {"html", "xhtml"}:
        return split_html_document(text, fallback), {}
    if source_format == "markdown":
        return split_markdown(text, fallback), {}
    if source_format == "txt":
        return split_txt(text, fallback), {}
    raise BookError(f"Extractor not implemented for {source_format}.")


def book_dir_for(slug: str) -> Path:
    return repo_root() / "books" / slug


def state_repository(book_dir: Path) -> WorkflowStateRepository:
    return WorkflowStateRepository(FilesystemStorage(book_dir))


def template_text(name: str, fallback: str) -> str:
    template = repo_root() / "docs" / "templates" / name
    if template.is_file():
        return template.read_text(encoding="utf-8")
    return fallback


def create_support_files(book_dir: Path) -> None:
    glossary_fallback = """# Glossary

| Original | Translation | Type | Notes |
| --- | --- | --- | --- |
"""
    style_fallback = """# Style Guide

## Narration

- Point of view:
- Narrative distance:
- Register:
- Typical sentence length and movement:

## Prose tendencies

- Terse or expansive:
- Plain or lexically rich:
- Restrained or expressive:

## Character voices

## Recurring stylistic decisions

## Ambiguities to preserve

## Review notes
"""
    (book_dir / "glossary.md").write_text(template_text("glossary.md", glossary_fallback), encoding="utf-8")
    (book_dir / "style-guide.md").write_text(template_text("style-guide.md", style_fallback), encoding="utf-8")


def workflow_provenance() -> dict[str, str | None]:
    provenance = {
        "repository": CANONICAL_REPOSITORY,
        "requested_ref": None,
        "resolved_revision": None,
    }
    install_path = repo_root() / ".book-translator-install.json"
    if not install_path.is_file():
        return provenance

    try:
        install = json.loads(install_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BookError(f"Invalid .book-translator-install.json: {exc}") from exc

    repository = install.get("canonical_repository")
    if repository:
        provenance["repository"] = str(repository)
    requested_ref = install.get("requested_ref")
    resolved_revision = install.get("resolved_revision")
    provenance["requested_ref"] = str(requested_ref) if requested_ref is not None else None
    provenance["resolved_revision"] = str(resolved_revision) if resolved_revision is not None else None
    return provenance


def extract_command(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise BookError(f"Source file does not exist: {source}")

    source_format = detect_format(source)
    slug = slugify(args.slug or source.stem)
    book_dir = book_dir_for(slug)
    if (book_dir / "metadata.json").exists() or (book_dir / "progress.json").exists():
        raise BookError(f"Book '{slug}' is already initialized; refusing to overwrite existing progress.")

    for directory in ("source", "extracted", "translated", "output"):
        (book_dir / directory).mkdir(parents=True, exist_ok=True)

    stored_source = book_dir / "source" / source.name
    if source != stored_source.resolve():
        if stored_source.exists():
            raise BookError(f"Source already exists and would be overwritten: {stored_source}")
        shutil.copy2(source, stored_source)

    chapters, detected = extract_chapters(stored_source, source_format)
    chapter_records: list[dict] = []
    used_slugs: set[str] = set()

    for number, chapter in enumerate(chapters, start=1):
        base_slug = slugify(chapter.title)[:80] or f"chapter-{number:03d}"
        chapter_slug = base_slug
        suffix = 2
        while chapter_slug in used_slugs:
            chapter_slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(chapter_slug)

        filename = f"{number:03d}-{chapter_slug}.md"
        source_rel = Path("extracted") / filename
        translation_rel = Path("translated") / filename
        (book_dir / source_rel).write_text(chapter.content, encoding="utf-8")
        chapter_records.append(
            {
                "number": number,
                "title": chapter.title,
                "slug": chapter_slug,
                "source_path": source_rel.as_posix(),
                "translation_path": translation_rel.as_posix(),
                "status": "extracted",
            }
        )

    workflow = workflow_provenance()
    workflow["review_evidence"] = REVIEW_EVIDENCE_VERSION
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "title": args.title or detected.get("title") or source.stem,
        "author": args.author or detected.get("author"),
        "source_language": args.source_language or detected.get("source_language") or "unknown",
        "target_language": args.target_language,
        "source_format": source_format,
        "source_file": source.name,
        "chapter_count": len(chapter_records),
        "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "workflow": workflow,
    }
    progress = {
        "schema_version": SCHEMA_VERSION,
        "book_slug": slug,
        "chapters": chapter_records,
    }
    review_ledger = {
        "schema_version": SCHEMA_VERSION,
        "book_slug": slug,
        "next_sequence": 1,
        "records": [],
    }

    repository = state_repository(book_dir)
    try:
        repository.create("metadata.json", SchemaKind.METADATA, metadata)
        repository.create("progress.json", SchemaKind.PROGRESS, progress)
        repository.create("review-ledger.json", SchemaKind.REVIEW_LEDGER, review_ledger)
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise BookError(f"Cannot write workflow state for books/{slug}: {exc}") from exc
    create_support_files(book_dir)

    print(f"Extracted {len(chapter_records)} chapter(s) into books/{slug}/extracted/")
    return 0


def load_book(slug: str) -> tuple[Path, dict, dict]:
    book_dir = book_dir_for(slug)
    if not book_dir.is_dir():
        raise BookError(f"Book directory does not exist: books/{slug}")
    metadata_path = book_dir / "metadata.json"
    progress_path = book_dir / "progress.json"
    if not metadata_path.is_file():
        raise BookError(f"Missing metadata.json for books/{slug}")
    if not progress_path.is_file():
        raise BookError(f"Missing progress.json for books/{slug}")

    repository = state_repository(book_dir)
    try:
        metadata = repository.read(
            "metadata.json",
            SchemaKind.METADATA,
            allow_legacy=True,
        ).data
        progress = repository.read(
            "progress.json",
            SchemaKind.PROGRESS,
            allow_legacy=True,
        ).data
    except (SchemaError, RepositoryError, StorageError) as exc:
        raise BookError(f"Invalid workflow state in books/{slug}: {exc}") from exc
    return book_dir, metadata, progress


def _review_artifact_reader(book_dir: Path):
    root = book_dir.resolve(strict=False)

    def read(relative_path: str) -> bytes:
        target = (root / relative_path).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise OSError(f"artifact path escapes book workspace: {relative_path}") from exc
        return target.read_bytes()

    return read


def validate_book(slug: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        book_dir, metadata, progress = load_book(slug)
    except BookError as exc:
        return [str(exc)], []

    for directory in ("source", "extracted", "translated", "output"):
        if not (book_dir / directory).is_dir():
            errors.append(f"Missing directory: {directory}/")

    for required in ("glossary.md", "style-guide.md"):
        if not (book_dir / required).is_file():
            errors.append(f"Missing {required}")

    source_file = metadata.get("source_file")
    if not source_file or not (book_dir / "source" / str(source_file)).is_file():
        errors.append(f"Source file declared in metadata.json does not exist: source/{source_file}")

    workflow = metadata.get("workflow")
    if workflow is None:
        warnings.append("metadata.json has no workflow provenance; this may be a legacy book workspace")
    elif not isinstance(workflow, dict):
        errors.append("metadata.json workflow must be an object")
    else:
        if workflow.get("repository") != CANONICAL_REPOSITORY:
            warnings.append(f"metadata workflow repository is {workflow.get('repository')!r}, expected {CANONICAL_REPOSITORY!r}")
        if not workflow.get("resolved_revision"):
            warnings.append("metadata workflow resolved_revision is unavailable; exact workflow reproducibility is not guaranteed")

    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        errors.append("progress.json must contain a chapters array")
        chapters = []

    if metadata.get("chapter_count") != len(chapters):
        errors.append(
            f"metadata chapter_count={metadata.get('chapter_count')} does not match progress chapters={len(chapters)}"
        )

    numbers = [chapter.get("number") for chapter in chapters if isinstance(chapter, dict)]
    if len(numbers) != len(set(numbers)):
        errors.append("Chapter numbers are not unique")
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        errors.append("Chapter numbers must be continuous and ordered from 1")

    slugs = [chapter.get("slug") for chapter in chapters if isinstance(chapter, dict)]
    if len(slugs) != len(set(slugs)):
        errors.append("Chapter slugs are not unique")

    referenced_extracted: set[str] = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            errors.append("Every chapter entry must be an object")
            continue
        number = chapter.get("number")
        status = chapter.get("status")
        source_path = chapter.get("source_path")
        translation_path = chapter.get("translation_path")

        if status not in ALLOWED_STATUSES:
            errors.append(f"Chapter {number}: invalid status '{status}'")

        if not source_path:
            errors.append(f"Chapter {number}: missing source_path")
        else:
            source_chapter = book_dir / source_path
            referenced_extracted.add(Path(source_path).as_posix())
            if status in {"extracted", "translated", "reviewed"} and not source_chapter.is_file():
                errors.append(f"Chapter {number}: missing extracted chapter {source_path}")

        if not translation_path:
            errors.append(f"Chapter {number}: missing translation_path")
        elif status in {"translated", "reviewed"}:
            translated = book_dir / translation_path
            if not translated.is_file():
                errors.append(f"Chapter {number}: status={status} but translation is missing: {translation_path}")
            elif not translated.read_text(encoding="utf-8").strip():
                errors.append(f"Chapter {number}: translation file is empty: {translation_path}")

    extracted_dir = book_dir / "extracted"
    if extracted_dir.is_dir():
        actual = {path.relative_to(book_dir).as_posix() for path in extracted_dir.glob("*.md")}
        for path in sorted(actual - referenced_extracted):
            errors.append(f"Extracted chapter is not referenced in progress.json: {path}")
        for path in sorted(referenced_extracted - actual):
            errors.append(f"progress.json references missing extracted chapter: {path}")

    if isinstance(workflow, dict) and workflow.get("review_evidence") == REVIEW_EVIDENCE_VERSION:
        repository = state_repository(book_dir)
        ledger_valid = False
        try:
            ledger = repository.read("review-ledger.json", SchemaKind.REVIEW_LEDGER)
        except (SchemaError, RepositoryError, StorageError) as exc:
            errors.append(f"review-ledger.json is required and must be valid: {exc}")
        else:
            if ledger.data.get("book_slug") != progress.get("book_slug"):
                errors.append(
                    "review-ledger.json book_slug does not match progress.json book_slug"
                )
            else:
                ledger_valid = True

        if ledger_valid:
            manager = ReviewLedgerManager(
                repository,
                artifact_reader=_review_artifact_reader(book_dir),
            )
            for chapter in chapters:
                if not isinstance(chapter, dict) or chapter.get("status") != "reviewed":
                    continue
                number = chapter.get("number")
                try:
                    resolution = manager.resolve_unit(progress, metadata, number)
                except ReviewEvidenceError as exc:
                    errors.append(f"Chapter {number}: review-ledger validation failed: {exc}")
                    continue
                if resolution.state != "pass":
                    errors.append(
                        f"Chapter {number}: status=reviewed requires current PASS review evidence; "
                        f"review state={resolution.state}"
                    )

    return errors, warnings


def validate_command(args: argparse.Namespace) -> int:
    errors, warnings = validate_book(args.slug)
    for warning in warnings:
        print(f"WARNING: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"books/{args.slug}: valid")
    return 0


def build_command(args: argparse.Namespace) -> int:
    errors, _ = validate_book(args.slug)
    if errors:
        raise BookError("Book structure is invalid; run validate first:\n- " + "\n- ".join(errors))

    book_dir, _, progress = load_book(args.slug)
    parts: list[str] = []
    incomplete: list[int] = []

    allowed = {"reviewed"} if not args.allow_unreviewed else {"translated", "reviewed"}
    for chapter in progress["chapters"]:
        if chapter["status"] not in allowed:
            incomplete.append(chapter["number"])
            continue
        translation = book_dir / chapter["translation_path"]
        if not translation.is_file() or not translation.read_text(encoding="utf-8").strip():
            incomplete.append(chapter["number"])
            continue
        parts.append(translation.read_text(encoding="utf-8").rstrip())

    if incomplete:
        requirement = "translated/reviewed" if args.allow_unreviewed else "reviewed"
        raise BookError(
            f"Cannot build: chapters not {requirement}: " + ", ".join(map(str, incomplete))
        )

    output_name = args.output or f"{args.slug}.md"
    output_path = book_dir / "output" / output_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(parts).rstrip() + "\n", encoding="utf-8")
    print(f"Built {len(parts)} chapter(s): {output_path.relative_to(repo_root()).as_posix()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Book Translator structural workflow helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract", help="Detect source format and create a book workspace.")
    extract.add_argument("source", help="Path to EPUB/HTML/XHTML/TXT/Markdown source file.")
    extract.add_argument("--slug", help="Book directory slug. Defaults to source filename.")
    extract.add_argument("--title", help="Override detected title.")
    extract.add_argument("--author", help="Override detected author.")
    extract.add_argument("--source-language", help="Override detected source language.")
    extract.add_argument("--target-language", required=True, help="Target language code/name, e.g. ru.")
    extract.set_defaults(func=extract_command)

    validate = subparsers.add_parser("validate", help="Validate one book workspace.")
    validate.add_argument("slug", help="Book slug under books/.")
    validate.set_defaults(func=validate_command)

    build = subparsers.add_parser("build", help="Build Markdown output from chapter translations.")
    build.add_argument("slug", help="Book slug under books/.")
    build.add_argument("--output", help="Output filename under books/<slug>/output/.")
    build.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="Allow building a preview from translated but not yet reviewed chapters.",
    )
    build.set_defaults(func=build_command)

    register_claim_commands(subparsers, repo_root())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BookError, ClaimCliError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())