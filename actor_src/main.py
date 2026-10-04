from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from apify import Actor

from .config import ConfigError, Settings
from .llm import LlmError, ManagedLlm
from .prompts import correction_messages, reviewer_messages, translator_messages


ROOT = Path(__file__).resolve().parents[1]
BOOK_SLUG = "actor-book"


class ActorRunError(RuntimeError):
    pass


async def run_cli(*args: str) -> str:
    command = [sys.executable, str(ROOT / "scripts" / "book.py"), *args]

    def execute() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )

    result = await asyncio.to_thread(execute)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ActorRunError(f"Book Translator CLI failed: {' '.join(args)}: {detail}")
    return result.stdout.strip()


def prepare_workflow_provenance(settings: Settings) -> None:
    payload = {
        "canonical_repository": "https://github.com/tim8es/book-translator",
        "requested_ref": settings.workflow_revision,
        "resolved_revision": settings.workflow_revision,
    }
    (ROOT / ".book-translator-install.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def input_location(actor_input: dict) -> str:
    uploads = actor_input.get("bookFiles") or []
    if len(uploads) != 1 or not isinstance(uploads[0], str):
        raise ActorRunError("Exactly one uploaded book is required per run")
    return uploads[0]


def safe_filename(location: str) -> str:
    parsed = urlparse(location)
    candidate = unquote(Path(parsed.path).name) if parsed.scheme else Path(location).name
    candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate).strip(" .")
    return candidate or "book.epub"


async def materialize_source(location: str) -> Path:
    target_dir = ROOT / ".actor-work"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_filename(location)

    parsed = urlparse(location)
    if parsed.scheme in {"http", "https"}:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(location)
            response.raise_for_status()
            target.write_bytes(response.content)
    else:
        source = Path(location).expanduser().resolve()
        if not source.is_file():
            raise ActorRunError(f"Uploaded source cannot be resolved: {location}")
        target.write_bytes(source.read_bytes())

    if target.stat().st_size == 0:
        raise ActorRunError("Source book is empty")
    return target


def load_progress() -> tuple[Path, dict]:
    book_dir = ROOT / "books" / BOOK_SLUG
    progress = json.loads((book_dir / "progress.json").read_text(encoding="utf-8"))
    return book_dir, progress


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


async def reserve_start_charge(settings: Settings) -> None:
    if settings.skip_charging:
        return
    result = await Actor.charge(event_name="book-started")
    charged = int(getattr(result, "charged_count", 0))
    if charged < 1 or bool(getattr(result, "event_charge_limit_reached", False)):
        raise ActorRunError("Run charge limit is insufficient to start this translation")


async def reserve_chapter_charge(settings: Settings, source_words: int) -> int:
    units = max(1, math.ceil(source_words / 1000))
    if settings.skip_charging:
        return units
    result = await Actor.charge(event_name="translation-1k-words", count=units)
    charged = int(getattr(result, "charged_count", 0))
    if charged < units or bool(getattr(result, "event_charge_limit_reached", False)):
        raise ActorRunError(
            f"Run charge limit is insufficient for the next chapter: required units={units}, charged={charged}"
        )
    return units


async def translate_once(
    llm: ManagedLlm,
    *,
    source: str,
    target_language: str,
    glossary: str,
    style_guide: str,
) -> str:
    return await llm.translation(
        translator_messages(
            source=source,
            target_language=target_language,
            glossary=glossary,
            style_guide=style_guide,
        )
    )


async def correct_once(
    llm: ManagedLlm,
    *,
    source: str,
    translation: str,
    issues: list[dict],
    target_language: str,
    glossary: str,
    style_guide: str,
) -> str:
    return await llm.translation(
        correction_messages(
            source=source,
            current_translation=translation,
            issues=issues,
            target_language=target_language,
            glossary=glossary,
            style_guide=style_guide,
        )
    )


async def accept_translation(
    book_dir: Path,
    chapter: dict,
    translation: str,
    session_id: str,
) -> None:
    number = str(chapter["number"])
    await run_cli(
        "claim",
        BOOK_SLUG,
        number,
        "--role",
        "translator",
        "--session-id",
        session_id,
    )
    try:
        target = book_dir / chapter["translation_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(translation.rstrip() + "\n", encoding="utf-8")
        await run_cli(
            "accept-translation",
            BOOK_SLUG,
            number,
            "--session-id",
            session_id,
        )
    finally:
        await run_cli("release", BOOK_SLUG, number, "--session-id", session_id)


async def review_translation(
    llm: ManagedLlm,
    *,
    book_dir: Path,
    chapter: dict,
    source: str,
    translation: str,
    target_language: str,
    glossary: str,
    style_guide: str,
    session_id: str,
) -> dict:
    number = str(chapter["number"])
    await run_cli(
        "claim",
        BOOK_SLUG,
        number,
        "--role",
        "reviewer",
        "--session-id",
        session_id,
    )
    try:
        result = await llm.review_json(
            reviewer_messages(
                source=source,
                translation=translation,
                target_language=target_language,
                glossary=glossary,
                style_guide=style_guide,
            )
        )
        await run_cli(
            "review-record",
            BOOK_SLUG,
            number,
            "--outcome",
            result["outcome"],
            "--session-id",
            session_id,
        )
        return result
    finally:
        await run_cli("release", BOOK_SLUG, number, "--session-id", session_id)


async def process_chapter(
    llm: ManagedLlm,
    settings: Settings,
    *,
    book_dir: Path,
    chapter: dict,
    target_language: str,
    glossary: str,
    style_guide: str,
) -> dict:
    source = (book_dir / chapter["source_path"]).read_text(encoding="utf-8")
    words = word_count(source)
    charged_units = await reserve_chapter_charge(settings, words)

    translation = await translate_once(
        llm,
        source=source,
        target_language=target_language,
        glossary=glossary,
        style_guide=style_guide,
    )
    translator_session = f"actor-translator-{chapter['number']}-{uuid.uuid4().hex[:10]}"
    await accept_translation(book_dir, chapter, translation, translator_session)

    for review_round in range(1, settings.max_review_rounds + 1):
        reviewer_session = f"actor-reviewer-{chapter['number']}-{review_round}-{uuid.uuid4().hex[:8]}"
        review = await review_translation(
            llm,
            book_dir=book_dir,
            chapter=chapter,
            source=source,
            translation=translation,
            target_language=target_language,
            glossary=glossary,
            style_guide=style_guide,
            session_id=reviewer_session,
        )
        if review["outcome"] == "PASS":
            await run_cli("accept-review", BOOK_SLUG, str(chapter["number"]))
            return {
                "chapter": chapter["number"],
                "words": words,
                "chargedUnits": charged_units,
                "reviewRounds": review_round,
                "status": "reviewed",
            }

        if review_round == settings.max_review_rounds:
            raise ActorRunError(
                f"Chapter {chapter['number']} did not pass review after "
                f"{settings.max_review_rounds} rounds"
            )

        translation = await correct_once(
            llm,
            source=source,
            translation=translation,
            issues=review["issues"],
            target_language=target_language,
            glossary=glossary,
            style_guide=style_guide,
        )
        correction_session = (
            f"actor-translator-{chapter['number']}-fix-{review_round}-{uuid.uuid4().hex[:8]}"
        )
        await accept_translation(book_dir, chapter, translation, correction_session)

    raise ActorRunError("Unreachable review state")


async def publish_outputs(book_dir: Path, output_format: str) -> list[str]:
    keys: list[str] = []
    if output_format in {"markdown", "both"}:
        await run_cli("build", BOOK_SLUG, "--format", "markdown")
        path = book_dir / "output" / f"{BOOK_SLUG}.md"
        await Actor.set_value("OUTPUT_MARKDOWN", path.read_text(encoding="utf-8"), content_type="text/markdown")
        keys.append("OUTPUT_MARKDOWN")

    if output_format in {"epub", "both"}:
        await run_cli("build", BOOK_SLUG, "--format", "epub")
        path = book_dir / "output" / f"{BOOK_SLUG}.epub"
        await Actor.set_value("OUTPUT_EPUB", path.read_bytes(), content_type="application/epub+zip")
        keys.append("OUTPUT_EPUB")
    return keys


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        settings = Settings.from_env()
        settings.validate_pricing()
        prepare_workflow_provenance(settings)

        llm = ManagedLlm(settings, settings.max_llm_cost_usd)

        try:
            await Actor.set_status_message("Preparing source")
            source = await materialize_source(input_location(actor_input))
            target_language = str(actor_input.get("targetLanguage") or "").strip()
            if not target_language:
                raise ActorRunError("targetLanguage is required")

            extract_args = [
                "extract",
                str(source),
                "--slug",
                BOOK_SLUG,
                "--target-language",
                target_language,
            ]
            for field, flag in (
                ("sourceLanguage", "--source-language"),
                ("title", "--title"),
                ("author", "--author"),
            ):
                value = str(actor_input.get(field) or "").strip()
                if value:
                    extract_args.extend([flag, value])

            await run_cli(*extract_args)
            book_dir, progress = load_progress()
            chapters = progress.get("chapters") or []
            if not chapters:
                raise ActorRunError("No translatable chapters were extracted")

            source_texts = [
                (chapter, (book_dir / chapter["source_path"]).read_text(encoding="utf-8"))
                for chapter in chapters
            ]
            total_words = sum(word_count(text) for _, text in source_texts)
            if total_words > settings.max_source_words:
                raise ActorRunError(
                    f"Source exceeds owner safety limit: {total_words} words > {settings.max_source_words}"
                )
            oversized = [
                chapter["number"]
                for chapter, text in source_texts
                if len(text) > settings.max_chapter_chars
            ]
            if oversized:
                raise ActorRunError(
                    "Chapter context exceeds the current safe MVP limit before chunking support: "
                    + ", ".join(map(str, oversized))
                )

            # Reserve the fixed run charge before the first paid LLM call.
            await reserve_start_charge(settings)

            glossary = (book_dir / "glossary.md").read_text(encoding="utf-8")
            style_guide = (book_dir / "style-guide.md").read_text(encoding="utf-8")
            results = []

            for index, chapter in enumerate(chapters, start=1):
                await Actor.set_status_message(
                    f"Translating and reviewing chapter {index}/{len(chapters)}"
                )
                result = await process_chapter(
                    llm,
                    settings,
                    book_dir=book_dir,
                    chapter=chapter,
                    target_language=target_language,
                    glossary=glossary,
                    style_guide=style_guide,
                )
                results.append(result)
                await Actor.push_data(result)

            await run_cli("validate", BOOK_SLUG)
            output_format = str(actor_input.get("outputFormat") or "epub")
            output_keys = await publish_outputs(book_dir, output_format)

            summary = {
                "status": "complete",
                "chapters": len(chapters),
                "sourceWords": total_words,
                "targetLanguage": target_language,
                "llmCostUsd": round(llm.spent_usd, 6),
                "llmCostCeilingUsd": llm.limit_usd,
                "outputKeys": output_keys,
                "usage": [
                    {
                        "model": item.model,
                        "role": item.role,
                        "inputTokens": item.input_tokens,
                        "outputTokens": item.output_tokens,
                        "costUsd": round(item.cost_usd, 6),
                    }
                    for item in llm.usage
                ],
            }
            await Actor.set_value("SUMMARY", summary)
            await Actor.push_data(summary)
            await Actor.set_status_message("Translation complete", is_terminal=True)
        finally:
            await llm.close()
