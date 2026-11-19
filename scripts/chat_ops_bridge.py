#!/usr/bin/env python3
"""Execute a durable, allowlisted Chat request against its pinned Book Translator engine.

The queue lives in the *book* repository. GitHub Actions is the only writer for
chat-results; each result and its book changes are pushed in ONE Git commit.
A rejected push is replayed only after a fresh checkout and full validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HEX32 = re.compile(r"^[0-9a-f]{32}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SLUG = re.compile(r"[^\W_][\w-]{0,99}\Z")
SESSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
OPERATIONS = {"claim", "renew-claim", "release", "accept-translation",
              "review-record", "accept-review", "cleanup-claims",
              "defer-review", "pause-auto", "resume-auto"}
REQUESTS = Path(".chat-ops/requests")
RESULTS = Path(".chat-ops/results")


class RequestError(ValueError):
    """Refuse malformed or stale operations without touching book state."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RequestError(reason)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strict_json(path: Path) -> dict:
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise RequestError("duplicate JSON key")
            output[key] = value
        return output
    try:
        obj = json.loads(path.read_text("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, OSError) as exc:
        raise RequestError("invalid request JSON") from exc
    require(type(obj) is dict, "request must be a JSON object")
    return obj


def validate(raw: dict, file_id: str) -> dict:
    require(type(raw) is dict, "request must be an object")
    common = {"schema_version", "request_id", "book_slug", "workflow_revision",
              "operation", "chapter", "session_id"}
    require(raw.get("schema_version") == 1 and type(raw.get("schema_version")) is int,
            "unsupported request schema")
    require(raw.get("request_id") == file_id and HEX32.fullmatch(file_id) is not None,
            "request id does not match immutable filename")
    require(isinstance(raw.get("book_slug"), str) and
            SLUG.fullmatch(raw["book_slug"]) is not None, "unsafe book slug")
    require(isinstance(raw.get("workflow_revision"), str) and
            SHA40.fullmatch(raw["workflow_revision"]) is not None, "invalid workflow revision")
    op = raw.get("operation")
    require(op in OPERATIONS if isinstance(op, str) else False, "operation not allowed")
    require(isinstance(raw.get("session_id"), str) and
            SESSION.fullmatch(raw["session_id"]) is not None, "unsafe session id")
    require(type(raw.get("chapter")) is int and 1 <= raw["chapter"] <= 100000,
            "chapter must be a positive integer")
    extras = {
        "claim": {"role", "base_commit", "shared_state"},
        "renew-claim": set(),
        "release": set(),
        "accept-translation": {"translation", "translation_sha256",
                               "source_sha256", "claim_sha256"},
        "review-record": {"outcome", "quality_report", "source_sha256",
                          "translation_sha256", "claim_sha256"},
        "accept-review": set(),
        "cleanup-claims": set(),
        "defer-review": {"reason"},
        "pause-auto": set(),
        "resume-auto": set(),
    }[op]
    expected=common|extras
    if op=="accept-translation":
        require(set(raw) in (expected,expected|{"structural_segments"}),"missing or unexpected request fields")
    elif op=="review-record":
        require(set(raw) in (expected,(expected-{"quality_report"})|{"addressed_parts"}),"missing or unexpected request fields")
    else:
        require(set(raw).issuperset(common) and set(raw).issubset(expected),"missing or unexpected request fields")
    if op == "defer-review":
        require(raw.get("reason") in {
            "correction-limit", "review-round-limit", "model-anchor-retries",
            "operator-attention",
        } if isinstance(raw.get("reason"), str) else False,
                "invalid or missing defer reason")
    if op == "claim":
        require(raw.get("role") in {"translator", "reviewer"}, "invalid claim role")
        if "base_commit" in raw:
            require(isinstance(raw["base_commit"], str) and
                    SHA40.fullmatch(raw["base_commit"]) is not None, "invalid base commit")
        if "shared_state" in raw:
            pair = raw["shared_state"]
            require(type(pair) is dict and set(pair) == {"glossary", "style_guide"} and
                    all(isinstance(v, str) and SHA40.fullmatch(v) for v in pair.values()),
                    "shared_state must contain Git blob SHAs")
            require("base_commit" in raw, "shared snapshot requires base commit")
    if op == "accept-translation":
        require(isinstance(raw["translation"], str) and
                0 < len(raw["translation"].encode("utf-8")) <= 300000 and
                raw["translation"].strip(), "invalid translation size")
        require(sha256(raw["translation"].encode("utf-8")) ==
                raw["translation_sha256"], "translation SHA-256 mismatch")
        if "structural_segments" in raw:
            rows=raw["structural_segments"]
            require(type(rows) is dict and 1<=len(rows)<=20000 and
                    all(re.fullmatch(r"s[0-9]{5}",k) and isinstance(v,str) and v.strip() for k,v in rows.items())
                    and len(json.dumps(rows,ensure_ascii=False).encode("utf-8"))<=600000,
                    "invalid structural segment response")
    if op == "review-record":
        require(raw["outcome"] in {"PASS", "CORRECTIONS_REQUIRED"}, "invalid outcome")
        if "addressed_parts" in raw:
            require(type(raw["addressed_parts"]) is list and 1<=len(raw["addressed_parts"])<=64 and
                    len(json.dumps(raw["addressed_parts"],ensure_ascii=False).encode("utf-8"))<=524288,
                    "invalid addressed review response size")
        else:
            require(type(raw["quality_report"]) is dict, "quality report is required")
            require(len(json.dumps(raw["quality_report"]).encode("utf-8")) <= 32768,
                    "quality report exceeds 32 KiB")
    if op in {"accept-translation", "review-record"}:
        for name in ("source_sha256", "claim_sha256"):
            require(isinstance(raw[name], str) and re.fullmatch(r"[0-9a-f]{64}", raw[name]),
                    "invalid " + name)
    if op == "review-record":
        require(isinstance(raw["translation_sha256"], str) and
                re.fullmatch(r"[0-9a-f]{64}", raw["translation_sha256"]),
                "invalid translation SHA-256")
    return raw


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args],
                          text=True, capture_output=True, check=check)


def cli(root: Path, script: str, *args: str) -> dict:
    if script not in {"book.py", "corpus.py"}:
        raise RequestError("unsupported canonical CLI script")
    active = _ACTIVE_PINNED_RUNTIME
    if active is None:
        # Synthetic unit fixtures may patch pinned_engine/cli without checkout.
        cmd = [sys.executable, str(root / "scripts" / script), *map(str, args)]
    else:
        pinned, revision, bound_root = active
        require(root.resolve() == bound_root, "pinned runtime belongs to another book-state checkout")
        command = Path(__file__).resolve().with_name("pinned_cli.py")
        cmd = [sys.executable, str(command), "--engine", str(pinned),
               "--state", str(root), "--revision", revision,
               script.removesuffix(".py"), *map(str, args)]
    result = subprocess.run(cmd, cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise RequestError("canonical command failed: " +
                           (result.stderr or result.stdout).strip()[-1000:])
    output = result.stdout.strip()
    try:
        return json.loads(output) if output.startswith("{") else {"message": output[-700:]}
    except ValueError:
        raise RequestError("canonical command returned invalid JSON") from None


def expected_book(root: Path, request: dict) -> tuple[Path, dict]:
    book = root / "books" / request["book_slug"]
    require(book.is_dir() and not book.is_symlink(), "book workspace is absent")
    metadata = strict_json(book / "metadata.json")
    workflow = metadata.get("workflow", {})
    require(type(workflow) is dict and
            workflow.get("resolved_revision") == request["workflow_revision"],
            "book workflow revision differs from request")
    return book, metadata


_PINNED_ENGINE_CACHE: dict[tuple[str, str], tuple[tempfile.TemporaryDirectory, Path]] = {}
_ACTIVE_PINNED_RUNTIME: tuple[Path, str, Path] | None = None


def pinned_engine(root: Path, engine: Path, revision: str) -> Path:
    """Prepare the book's exact public workflow without modifying state code.

    The ChatOps executor itself may have a newer, separately locked version;
    never check out an old literary workflow over the code that's currently
    running. A temporary clone is reused by this process for the same book pin.
    """
    global _ACTIVE_PINNED_RUNTIME
    helpers = str(Path(__file__).resolve().parent)
    if helpers not in sys.path:
        sys.path.insert(0, helpers)
    from pinned_runtime import prepare, git_head
    root = root.resolve(strict=True)
    engine = engine.resolve(strict=True)
    if not SHA40.fullmatch(revision):
        raise RequestError("invalid book workflow revision")
    key = (str(root), revision)
    if key in _PINNED_ENGINE_CACHE:
        pinned = _PINNED_ENGINE_CACHE[key][1]
    elif git_head(engine) == revision:
        pinned = engine  # isolated fixture or exact book pin; no clone required
    else:
        temp = tempfile.TemporaryDirectory(prefix="chat-pinned-workflow-")
        pinned = Path(temp.name) / "public-engine"
        clone = subprocess.run(
            ["git", "clone", "--quiet", "https://github.com/tim8es/book-translator.git",
             str(pinned)], capture_output=True, text=True, timeout=180)
        if clone.returncode:
            temp.cleanup()
            raise RequestError("cannot fetch book-pinned public workflow")
        checkout = subprocess.run(
            ["git", "-C", str(pinned), "checkout", "--quiet", "--detach", revision],
            capture_output=True, text=True, timeout=50)
        if checkout.returncode:
            temp.cleanup()
            raise RequestError("book-pinned public workflow revision is unavailable")
        _PINNED_ENGINE_CACHE[key] = (temp, pinned)
    try:
        prepare(pinned, root, revision)
    except (OSError, ValueError, RuntimeError) as exc:
        _ACTIVE_PINNED_RUNTIME = None
        raise RequestError("cannot prepare isolated book-pinned workflow: " + str(exc)) from exc
    _ACTIVE_PINNED_RUNTIME = (pinned, revision, root)
    return pinned

def chapter_and_claim(book: Path, request: dict, *, role: str) -> dict:
    progress = strict_json(book / "progress.json")
    chapter = next((x for x in progress.get("chapters", [])
                    if x.get("number") == request["chapter"]), None)
    require(chapter is not None, "chapter not in progress")
    claim_path = book / ".workflow" / "claims" / (chapter["unit_id"] + ".json")
    require(claim_path.is_file(), "no durable claim")
    claim_bytes = claim_path.read_bytes()
    require(sha256(claim_bytes) == request["claim_sha256"], "claim revision changed")
    claim = strict_json(claim_path)
    require(claim.get("unit_id") == chapter["unit_id"] and
            claim.get("session_id") == request["session_id"] and
            claim.get("role") == role and
            claim.get("workflow_revision") == request["workflow_revision"],
            "claim owner/role/revision mismatch")
    expiry = claim.get("expires_at", "")
    try:
        when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        require(when.tzinfo is not None and when > datetime.now(timezone.utc),
                "claim is expired")
    except (ValueError, TypeError) as exc:
        raise RequestError("invalid claim expiry") from exc
    source = book / chapter["source_path"]
    require(source.is_file() and sha256(source.read_bytes()) == request["source_sha256"],
            "source artifact SHA-256 mismatch")
    return chapter


def span_helpers():
    """Load the shared public runtime, not a second literary workflow."""
    directory=str(Path(__file__).resolve().parents[1]/"automation")
    if directory not in sys.path:
        sys.path.insert(0,directory)
    import book_runner
    import span_map
    import span_exchange
    return book_runner,span_map,span_exchange


def execute(root: Path, engine: Path, request: dict) -> dict:
    global _ACTIVE_PINNED_RUNTIME
    _ACTIVE_PINNED_RUNTIME = None  # never inherit a previous request's pin
    book, _ = expected_book(root, request)
    if request["operation"] in {"pause-auto", "resume-auto"}:
        # Only change scheduling preference; never mutate chapter claims or evidence.
        mode = "chatops" if request["operation"] == "pause-auto" else "auto"
        control = book / ".workflow" / "auto-control.json"
        control.parent.mkdir(parents=True, exist_ok=True)
        control.write_text(json.dumps({
            "schema_version": 1, "mode": mode,
            "updated_by": request["session_id"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"auto_mode": mode}
    pinned=pinned_engine(root, engine, request["workflow_revision"])
    slug = request["book_slug"]
    chapter = str(request["chapter"])
    session = request["session_id"]
    op = request["operation"]
    # Full preflight precedes EVERY operation, including claims.
    cli(root, "book.py", "validate", slug)
    cli(root, "corpus.py", "verify", slug)
    if op == "claim":
        args = [slug, chapter, "--role", request["role"], "--session-id",
                session, "--json"]
        if "base_commit" in request:
            args += ["--base-commit", request["base_commit"]]
        if "shared_state" in request:
            pair = request["shared_state"]
            for filename, key in (("glossary.md", "glossary"),
                                  ("style-guide.md", "style_guide")):
                data = (book / filename).read_bytes()
                blob_sha = git(root, "hash-object", str(book / filename)).stdout.strip()
                require(blob_sha == pair[key], "frozen Git revision changed: " + filename)
                args += ["--" + ("glossary" if key == "glossary" else "style-guide") +
                         "-revision", sha256(data)]
        return cli(root, "book.py", "claim", *args)
    if op == "cleanup-claims":
        return cli(root, "book.py", "cleanup-claims", slug, "--json")
    if op == "defer-review":
        return cli(root, "book.py", "defer-review", slug, chapter,
                   "--reason", request["reason"], "--json")
    if op in {"renew-claim", "release"}:
        return cli(root, "book.py", op, slug, chapter, "--session-id", session, "--json")
    if op == "accept-translation":
        unit = chapter_and_claim(book, request, role="translator")
        path = book / unit["translation_path"]
        require(path.resolve().is_relative_to(book.resolve()) and
                not path.is_symlink(), "unsafe translation target")
        mapping=None
        if "structural_segments" in request:
            runner,maps,exchange=span_helpers()
            original=(book/unit["source_path"]).read_bytes().decode("utf-8")
            try:
                helper=runner.load_public_structural_helper(pinned)
                mapping=exchange.translation_map(original,request["translation"],request["structural_segments"],
                                                helper,runner.bounded_source_plan)
                runner.check_markers(original,request["translation"])
            except (ValueError,KeyError,TypeError,RuntimeError) as exc:
                raise RequestError("structural segment evidence rejected") from exc
        # Not durable until canonical acceptance, shared map and receipt push together.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(request["translation"].encode("utf-8"))
        result=cli(root, "book.py", "accept-translation", slug, chapter,
                   "--session-id", session, "--json")
        if mapping is not None:
            maps.persist_artifact(book,unit["unit_id"],mapping,original,path.read_bytes().decode("utf-8"))
        return result
    if op == "review-record":
        unit = chapter_and_claim(book, request, role="reviewer")
        translation = book / unit["translation_path"]
        require(translation.is_file() and sha256(translation.read_bytes()) ==
                request["translation_sha256"], "translation artifact changed")
        if "addressed_parts" in request:
            runner,maps,exchange=span_helpers()
            original=(book/unit["source_path"]).read_bytes().decode("utf-8")
            current=translation.read_bytes().decode("utf-8")
            try:
                mapping=maps.read_artifact(book,unit["unit_id"],original,current)
                require(mapping is not None,"current accepted artifact has no span map")
                ledger=strict_json(book/"review-ledger.json")
                previous=runner.latest_record(ledger,unit)
                report=exchange.compile_review(original,current,mapping,previous,request["addressed_parts"],request["outcome"])
            except (ValueError,KeyError,TypeError,RuntimeError) as exc:
                raise RequestError("addressed reviewer evidence rejected") from exc
        else:
            report=request["quality_report"]
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "quality-report.json"
            evidence.write_text(json.dumps(report, ensure_ascii=False),encoding="utf-8")
            return cli(root, "book.py", "review-record", slug, chapter,
                       "--outcome", request["outcome"], "--session-id", session,
                       "--quality-report", str(evidence), "--json")
    if op == "accept-review":
        return cli(root, "book.py", "accept-review", slug, chapter, "--json")
    raise RequestError("unknown operation")


def reset(root: Path) -> None:
    git(root, "fetch", "--quiet", "origin", "main")
    git(root, "reset", "--hard", "origin/main")
    git(root, "clean", "-fd")


def process(root: Path, engine: Path, request_id: str) -> str:
    """Retries only a *rejected push*, never an ambiguous successful write."""
    for attempt in range(3):
        reset(root)
        request = None
        receipt_path = root / RESULTS / (request_id + ".json")
        if receipt_path.exists():
            return "already-recorded"
        request_path = root / REQUESTS / (request_id + ".json")
        if not request_path.is_file():
            return "request-disappeared"
        book_slug = None
        try:
            request = validate(strict_json(request_path), request_id)
            book_slug = request["book_slug"]
            outcome = execute(root, engine, request)
            # Literary mutations must pass canonical corpus preflight. An
            # operator's book-scoped Auto control changes no literary state
            # and must not depend on a temporary (un-pinned) engine checkout.
            if request["operation"] not in {"pause-auto", "resume-auto"}:
                cli(root, "book.py", "validate", book_slug)
                cli(root, "corpus.py", "verify", book_slug)
            status = "ok"
            error = None
        except (RequestError, OSError, subprocess.CalledProcessError, KeyError,
                TypeError, ValueError) as exc:
            status = "failed"
            outcome = None
            error = str(exc)[:1200]
            # No partial chapter mutation may accompany a failed request.
            git(root, "reset", "--hard", "HEAD")
            git(root, "clean", "-fd")
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {"schema_version": 1, "request_id": request_id,
                   "status": status, "operation": request.get("operation") if
                   request is not None else None, "result": outcome, "error": error}
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False,
                                           indent=2) + "\n", encoding="utf-8")
        paths = [str(RESULTS / (request_id + ".json"))]
        if status == "ok" and book_slug is not None:
            paths.append("books/" + book_slug)
        git(root, "add", "-A", "--", *paths)
        git(root, "-c", "user.name=book-translator-bot",
            "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", "chat-ops: " + status + " " + request_id)
        pushed = git(root, "push", "origin", "HEAD:main", check=False)
        if pushed.returncode == 0:
            # Re-read the persisted receipt from the remote branch, not our
            # local worktree. The receipt and book mutation share one commit.
            git(root, "fetch", "--quiet", "origin", "main")
            remote = git(root, "show",
                         "origin/main:" + str(RESULTS / (request_id + ".json")),
                         check=False)
            if remote.returncode != 0 or remote.stdout != receipt_path.read_text("utf-8"):
                raise RuntimeError("published receipt could not be verified")
            reset(root)  # discard temporary pinned scripts and install marker
            return status
        # Git transports can fail after a successful push. Check the remote
        # receipt before considering replay; never blindly re-run an operation.
        refreshed = git(root, "fetch", "--quiet", "origin", "main", check=False)
        if refreshed.returncode != 0:
            raise RuntimeError("push failed and remote state cannot be verified")
        remote = git(root, "show",
                     "origin/main:" + str(RESULTS / (request_id + ".json")),
                     check=False)
        if remote.returncode == 0:
            if remote.stdout != receipt_path.read_text("utf-8"):
                raise RuntimeError("another writer published a conflicting receipt")
            reset(root)
            return status
        # Only a proven stale-head rejection is eligible for full revalidation.
        if not any(token in pushed.stderr.lower() for token in
                   ("non-fast-forward", "fetch first", "stale info")):
            raise RuntimeError("push was rejected for a reason other than concurrent head drift")
    raise RuntimeError("unable to publish request after 3 fresh-state attempts")


def request_group(path: Path) -> str:
    """Route malformed envelopes to an isolated failure-receipt lane."""
    try:
        data = strict_json(path)
        slug = data.get("book_slug")
        if isinstance(slug, str) and SLUG.fullmatch(slug):
            return slug
    except (RequestError, OSError):
        pass
    return "__invalid__"


def pending(root: Path, book_slug: str | None = None) -> list[str]:
    path = root / REQUESTS
    if not path.is_dir():
        return []
    return sorted(p.stem for p in path.glob("*.json") if HEX32.fullmatch(p.stem)
                  and not (root / RESULTS / p.name).exists()
                  and (book_slug is None or request_group(p) == book_slug))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--book-slug", help="Only process pending requests for this book")
    args = parser.parse_args()
    root = args.root.resolve()
    engine = args.engine.resolve()
    require(args.max_requests in range(1, 501), "invalid max requests")
    require(args.book_slug is None or args.book_slug == "__invalid__" or
            SLUG.fullmatch(args.book_slug) is not None, "invalid book scope")
    reset(root)
    ids = pending(root, args.book_slug)[:args.max_requests]
    print("Pending Chat requests:", len(ids))
    for request_id in ids:
        outcome = process(root, engine, request_id)
        print(request_id, outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
