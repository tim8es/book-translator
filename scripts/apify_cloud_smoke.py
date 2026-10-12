#!/usr/bin/env python3
"""Validate a PRIVATE Book Translator beta in Apify with actual owner LLM calls.

Run only with an authenticated APIFY_TOKEN and after the private Actor's owner
LLM gateway secret has been configured. Reads no LLM credentials itself.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = "https://api.apify.com/v2"
ACTOR_NAME = "book-translator-managed-llm"
FIXTURE_URL = (
    "https://raw.githubusercontent.com/tim8es/book-translator/"
    "apify-managed-llm-mvp/examples/apify-smoke-book.txt"
)
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class SmokeError(RuntimeError):
    pass


def api_request(token: str, path: str, *, method="GET", payload=None) -> bytes:
    url = API + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=90) as response:
            return response.read()
    except HTTPError as exc:
        # The error response may echo signed upload URLs, credentials or input
        # body; never print raw server text into CI logs.
        raise SmokeError(f"Apify HTTP {exc.code} for {method} {path}") from None
    except URLError as exc:
        raise SmokeError(f"Apify request failed for {method} {path}: {type(exc.reason).__name__}") from None


def api_json(token: str, path: str, *, method="GET", payload=None) -> dict:
    return json.loads(api_request(token, path, method=method, payload=payload))


def find_private_actor(token: str) -> str:
    offset = 0
    while True:
        page = api_json(token, f"/actors?{urlencode({'my': 1, 'limit': 100, 'offset': offset})}")
        result = page.get("data") or {}
        rows = result.get("items") or []
        for row in rows:
            if row.get("name") != ACTOR_NAME:
                continue
            actor_id = row.get("id")
            if not actor_id:
                raise SmokeError("Actor record has no ID")
            details = api_json(token, f"/actors/{quote(actor_id, safe='')}").get("data") or {}
            if details.get("isPublic") is not False:
                raise SmokeError("Refusing smoke run: Actor privacy is not confirmed as private")
            return actor_id
        if not rows or len(rows) < 100:
            break
        offset += len(rows)
    raise SmokeError(f"Private Actor '{ACTOR_NAME}' not found; deploy the beta first")


def run_smoke(token: str, actor_id: str) -> tuple[str, dict]:
    run_input = {
        "bookFiles": [FIXTURE_URL],
        "targetLanguage": "Russian",
        "sourceLanguage": "English",
        "title": "The Lantern Window",
        "author": "Book Translator smoke fixture",
        "outputFormat": "both",
    }
    started = api_json(
        token,
        f"/actors/{quote(actor_id, safe='')}/runs?{urlencode({'build': 'beta', 'memory': 512, 'timeout': 900})}",
        method="POST",
        payload=run_input,
    ).get("data") or {}
    run_id = started.get("id")
    if not run_id:
        raise SmokeError("Apify did not return a run ID")
    print(f"Private cloud smoke run: {run_id}", flush=True)

    deadline = time.monotonic() + 1000
    while time.monotonic() < deadline:
        record = api_json(token, f"/actor-runs/{quote(run_id, safe='')}?waitForFinish=30").get("data") or {}
        status = record.get("status")
        print(f"Apify run status: {status}", flush=True)
        if status in TERMINAL:
            return run_id, record
        time.sleep(5)
    raise SmokeError(f"Run {run_id} did not finish before deadline; check Apify Console")


def check_artifacts(token: str, run_id: str, record: dict) -> None:
    if record.get("status") != "SUCCEEDED":
        raise SmokeError(f"Run {run_id} ended as {record.get('status')}, not SUCCEEDED")
    kv_id = record.get("defaultKeyValueStoreId")
    if not kv_id:
        raise SmokeError("Cloud run has no key-value store")

    kv = f"/key-value-stores/{quote(kv_id, safe='')}"
    summary = api_json(token, kv + "/records/SUMMARY")
    keys_page = api_json(token, kv + "/keys")
    keys = {row["key"] for row in (keys_page.get("data") or {}).get("items", [])}
    expected = {"SUMMARY", "CHAPTER_000001", "CHAPTER_000002", "OUTPUT_EPUB", "OUTPUT_MARKDOWN"}
    if missing := expected - keys:
        raise SmokeError("Missing required artifacts: " + ", ".join(sorted(missing)))
    if summary.get("status") != "complete" or summary.get("chapters") != 2:
        raise SmokeError("Run SUMMARY did not report two completed chapters")
    if set(summary.get("outputKeys") or []) != {"OUTPUT_EPUB", "OUTPUT_MARKDOWN"}:
        raise SmokeError("SUMMARY did not link both output files")
    if not (summary.get("llmCostUsd") or 0) > 0:
        raise SmokeError("No measured or estimated owner LLM cost in SUMMARY")

    epub = api_request(token, kv + "/records/OUTPUT_EPUB")
    with zipfile.ZipFile(io.BytesIO(epub)) as archive:
        if archive.read("mimetype") != b"application/epub+zip":
            raise SmokeError("Output is not a valid EPUB MIME ZIP")
        if archive.testzip() is not None:
            raise SmokeError("Output EPUB ZIP has a corrupt entry")
    markdown = api_request(token, kv + "/records/OUTPUT_MARKDOWN").decode("utf-8")
    if not any("\u0400" <= ch <= "\u04ff" for ch in markdown):
        raise SmokeError("Translated Markdown appears not to contain Russian text")

    safe_usage = [
        {
            "model": entry.get("model"),
            "role": entry.get("role"),
            "inputTokens": entry.get("inputTokens"),
            "outputTokens": entry.get("outputTokens"),
            "costUsd": entry.get("costUsd"),
            "costSource": entry.get("costSource"),
        }
        for entry in (summary.get("usage") or [])
    ]
    print(
        json.dumps(
            {
                "status": "PASS",
                "runId": run_id,
                "chapters": summary["chapters"],
                "sourceWords": summary.get("sourceWords"),
                "ownerLlmUsd": summary["llmCostUsd"],
                "platformUsageUsd": record.get("usageTotalUsd"),
                "usage": safe_usage,
                "artifacts": sorted(expected),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def main() -> int:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        print("Missing APIFY_TOKEN GitHub Actions secret", file=sys.stderr)
        return 2
    try:
        actor_id = find_private_actor(token)
        run_id, record = run_smoke(token, actor_id)
        check_artifacts(token, run_id, record)
    except (SmokeError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        # Our own exceptions contain no provider response bodies or secrets.
        print(f"CLOUD SMOKE FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
