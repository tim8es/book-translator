from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

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


async def ensure_public_remote_url(location: str) -> None:
    parsed = urlparse(location)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ActorRunError("Uploaded file location must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ActorRunError("Credentials embedded in uploaded file URLs are not allowed")

    host = parsed.hostname
    if host.lower() == "localhost":
        raise ActorRunError("Local/private upload URLs are not allowed")

    def resolve() -> list[str]:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return sorted({item[4][0] for item in infos})

    try:
        addresses = await asyncio.to_thread(resolve)
    except OSError as exc:
        raise ActorRunError(f"Cannot resolve uploaded file host: {host}") from exc

    if not addresses:
        raise ActorRunError(f"Uploaded file host resolved to no addresses: {host}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ActorRunError(
                f"Uploaded file URL resolves to a non-public address: {address}"
            )


async def materialize_source(location: str, settings: Settings) -> Path:
    target_dir = ROOT / ".actor-work"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_filename(location)

    parsed = urlparse(location)
    if parsed.scheme in {"http", "https"}:
        current = location
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
            for _ in range(6):
                await ensure_public_remote_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        redirect = response.headers.get("location")
                        if not redirect:
                            raise ActorRunError("Upload URL redirect has no Location header")
                        current = urljoin(current, redirect)
                        continue

                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared:
                        try:
                            declared_size = int(declared)
                        except ValueError as exc:
                            raise ActorRunError("Remote source returned an invalid Content-Length") from exc
                        if declared_size > settings.max_source_bytes:
                            raise ActorRunError(
                                f"Source exceeds owner byte-size limit: {declared_size} > {settings.max_source_bytes}"
                            )

                    written = 0
                    with target.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            written += len(chunk)
                            if written > settings.max_source_bytes:
                                raise ActorRunError(
                                    f"Source exceeds owner byte-size limit: > {settings.max_source_bytes}"
                                )
                            stream.write(chunk)
                    break
            else:
                raise ActorRunError("Too many redirects while fetching uploaded source")
    else:
        if not settings.skip_charging:
            raise ActorRunError("Local source paths are disabled in production runs")
        source = Path(location).expanduser().resolve()
        if not source.is_file():
            raise ActorRunError(f"Uploaded source cannot be resolved: {location}")
        if source.stat().st_size > settings.max_source_bytes:
            raise ActorRunError(
                f"Source exceeds owner byte-size limit: {source.stat().st_size} > {settings.max_source_bytes}"
            )
        target.write_bytes(source.read_bytes())

    if not target.is_file() or target.stat().st_size == 0:
        raise ActorRunError("Source book is empty")
    return target


def load_progress() -> tuple[Path, dict]:
    book_dir = ROOT / "books" / BOOK_SLUG
    progress = json.loads((book_dir / "progress.json").read_text(encoding="utf-8"))
    return book_dir, progress


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _markdown_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().replace("|", "\\|")


def apply_literary_memory(book_dir: Path, review: dict) -> None:
    glossary_path = book_dir / "glossary.md"
    glossary = glossary_path.read_text(encoding="utf-8")
    rows: list[str] = []
    for item in review.get("glossary_additions") or []:
        row = (
            f"| {_markdown_cell(item['original'])} | "
            f"{_markdown_cell(item['translation'])} | "
            f"{_markdown_cell(item.get('type', 'term'))} | "
            f"{_markdown_cell(item.get('notes', ''))} |"
        )
        if row not in glossary:
            rows.append(row)
    if rows:
        glossary = glossary.rstrip() + "\n" + "\n".join(rows) + "\n"
        glossary_path.write_text(glossary, encoding="utf-8")

    observations = review.get("style_observations") or []
    if observations:
        style_path = book_dir / "style-guide.md"
        style = style_path.read_text(encoding="utf-8")
        heading = "## Actor observations"
        if heading not in style:
            style = style.rstrip() + f"\n\n{heading}\n"
        additions = []
        for observation in observations:
            clean = re.sub(r"\s+", " ", str(observation)).strip()
            bullet = f"- {clean}"
            if clean and bullet not in style:
                additions.append(bullet)
        if additions:
            style = style.rstrip() + "\n" + "\n".join(additions) + "\n"
            style_path.write_text(style, encoding="utf-8")


def charging_manager():
    return Actor.get_charging_manager()


def ensure_book_charge_capacity(settings: Settings, chapter_word_counts: list[int]) -> None:
    if settings.skip_charging:
        return

    manager = charging_manager()
    pricing = manager.get_pricing_info()
    if not pricing.is_pay_per_event:
        raise ActorRunError("Production runs require Apify pay-per-event pricing")

    prices = pricing.per_event_prices
    missing = [
        name
        for name in ("book-started", "translation-1k-words")
        if name not in prices
    ]
    if missing:
        raise ActorRunError(
            "Missing configured PPE event price(s): " + ", ".join(missing)
        )

    word_units = sum(max(1, math.ceil(words / 1000)) for words in chapter_word_counts)
    required = prices["book-started"] + prices["translation-1k-words"] * word_units
    already_charged = manager.calculate_total_charged_amount()
    remaining = pricing.max_total_charge_usd - already_charged
    if required > remaining:
        raise ActorRunError(
            f"Run max charge is too low for the full book: "
            f"required=${required}, remaining=${remaining}"
        )


async def charge_book_start(settings: Settings) -> None:
    if settings.skip_charging:
        return
    result = await Actor.charge(event_name="book-started")
    charged = int(getattr(result, "charged_count", 0))
    if charged < 1:
        raise ActorRunError("The book-started event could not be charged")


def ensure_chapter_charge_capacity(settings: Settings, source_words: int) -> int:
    units = max(1, math.ceil(source_words / 1000))
    if settings.skip_charging:
        return units
    manager = charging_manager()
    available = manager.calculate_max_event_charge_count_within_limit(
        "translation-1k-words"
    )
    if available is not None and available < units:
        raise ActorRunError(
            f"Run charge limit cannot cover the next chapter: "
            f"required units={units}, available={available}"
        )
    return units


async def charge_completed_chapter(settings: Settings, units: int) -> None:
    if settings.skip_charging:
        return
    charged = 0
    for _ in range(units):
        result = await Actor.charge(event_name="translation-1k-words")
        charged_now = int(getattr(result, "charged_count", 0))
        if charged_now < 1:
            raise ActorRunError(
                f"Reviewed chapter was saved but only {charged}/{units} billing units "
                "could be charged; stopping to prevent unpaid additional work"
            )
        charged += charged_now


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
    charged_units = ensure_chapter_charge_capacity(settings, words)

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
            apply_literary_memory(book_dir, review)
            chapter_key = f"CHAPTER_{int(chapter['number']):06d}"
            chapter_path = book_dir / chapter["translation_path"]
            await Actor.set_value(
                chapter_key,
                chapter_path.read_text(encoding="utf-8"),
                content_type="text/markdown",
            )
            await charge_completed_chapter(settings, charged_units)
            return {
                "chapter": chapter["number"],
                "words": words,
                "chargedUnits": charged_units,
                "reviewRounds": review_round,
                "status": "reviewed",
                "outputKey": chapter_key,
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
        completed_results: list[dict] = []
        current_chapter: int | None = None

        try:
            await Actor.set_status_message("Preparing source")
            source = await materialize_source(input_location(actor_input), settings)
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

            chapter_word_counts = [
                word_count(text) for _, text in source_texts
            ]
            # Confirm the user's per-run limit can cover the complete planned
            # translation before any owner-funded LLM work begins.
            ensure_book_charge_capacity(settings, chapter_word_counts)
            await charge_book_start(settings)

            for index, chapter in enumerate(chapters, start=1):
                current_chapter = int(chapter["number"])
                glossary = (book_dir / "glossary.md").read_text(encoding="utf-8")
                style_guide = (book_dir / "style-guide.md").read_text(encoding="utf-8")
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
                completed_results.append(result)
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
                        "cachedInputTokens": item.cached_input_tokens,
                        "outputTokens": item.output_tokens,
                        "costUsd": round(item.cost_usd, 6),
                    }
                    for item in llm.usage
                ],
            }
            await Actor.set_value("SUMMARY", summary)
            await Actor.push_data(summary)
            await Actor.set_status_message("Translation complete", is_terminal=True)
        except Exception as exc:
            message = re.sub(r"https?://\\S+", "<remote-url>", str(exc))
            failure = {
                "status": "failed",
                "errorType": type(exc).__name__,
                "message": message[:1000],
                "currentChapter": current_chapter,
                "completedChapters": [item["chapter"] for item in completed_results],
                "llmCostUsd": round(llm.spent_usd, 6),
                "llmCostCeilingUsd": llm.limit_usd,
            }
            try:
                await Actor.set_value("SUMMARY", failure)
                await Actor.push_data(failure)
                await Actor.set_status_message(
                    f"Translation failed after {len(completed_results)} reviewed chapter(s)",
                    is_terminal=True,
                )
            except Exception:
                # Failure reporting must never hide the original processing error.
                pass
            raise
        finally:
            await llm.close()
