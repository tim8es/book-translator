#!/usr/bin/env python3
"""Dispatch the pinned public book/corpus CLI against an external state checkout.

For historical immutable revisions whose book.py resolves its root from
__file__, the pinned checkout itself is the runtime root and its books/ entry
is a symlink to the state checkout. Only git HEAD queries are redirected to
the state Git repository. All code, docs, and provenance markers stay in the
pinned public checkout; the book's stored workflow pin is never rewritten.
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

from pinned_runtime import prepare


def run_proposal(state: Path, revision: str, arguments: list[str]) -> int:
    """Transport only: all shared-state writes use the pinned proposal manager."""
    import re
    from workflow.filesystem import FilesystemStorage
    from workflow.repository import WorkflowStateRepository
    from workflow.proposals import ProposalManager, validate_proposal
    from workflow.shared_state import require_current_shared_state

    if len(arguments) != 1:
        raise ValueError("expected one proposal request file")
    request = json.loads(Path(arguments[0]).read_text("utf-8"))
    slug = request["book_slug"]
    if not isinstance(slug, str) or not re.fullmatch(r"[^\W_][\w-]{0,99}", slug):
        raise ValueError("invalid book slug")
    book = state / "books" / slug
    if not book.is_dir() or book.is_symlink():
        raise ValueError("unsafe book workspace")
    metadata = json.loads((book / "metadata.json").read_text("utf-8"))
    if metadata["workflow"]["resolved_revision"] != revision or request["workflow_revision"] != revision:
        raise ValueError("book workflow revision mismatch")
    progress = json.loads((book / "progress.json").read_text("utf-8"))
    unit = next(c for c in progress["chapters"] if c["number"] == request["chapter"])
    unit_id = unit["unit_id"]
    if not re.fullmatch(r"chapter-[0-9]{6}", unit_id):
        raise ValueError("invalid unit id")
    storage = FilesystemStorage(book)
    manager = ProposalManager(WorkflowStateRepository(storage))
    if request["operation"] == "proposal-submit":
        claim = json.loads(storage.read(".workflow/claims/" + unit_id + ".json").content)
        require_current_shared_state(storage, claim["shared_state_revisions"])
        result = manager.submit(
            unit_id, session_id=request["session_id"], target=request["target"],
            suggestion=request["suggestion"], rationale=request["rationale"])
        output = {"proposal_id": result.proposal["proposal_id"],
                  "target": result.proposal["target"], "unit_id": unit_id}
    elif request["operation"] == "proposal-reconcile":
        proposal_id = request["proposal_id"]
        if not re.fullmatch(r"[0-9a-f]{32}", proposal_id):
            raise ValueError("invalid proposal id")
        proposal = validate_proposal(json.loads(
            storage.read(".workflow/proposals/" + proposal_id + ".json").content))
        if (proposal["unit_id"] != unit_id or proposal["workflow_revision"] != revision
                or proposal["proposal_id"] != proposal_id):
            raise ValueError("proposal scope mismatch")
        accept = request["decision"] == "accept"
        if request["decision"] not in {"accept", "reject"}:
            raise ValueError("invalid proposal decision")
        result = manager.reconcile(
            proposal_id, session_id=request["session_id"], accept=accept,
            replacement=proposal["suggestion"].encode("utf-8") if accept else None,
            reason=request.get("reason"))
        output = {"proposal_id": proposal_id, "unit_id": unit_id,
                  "status": result.resolution["status"],
                  "target": result.resolution["target"],
                  "changed_shared_state": result.changed_shared_state,
                  "resulting_sha256": result.resolution["resulting_sha256"],
                  "reason": result.resolution["reason"]}
    else:
        raise ValueError("unsupported proposal operation")
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


def run(engine: Path, state: Path, revision: str, tool: str,
        arguments: list[str]) -> int:
    pinned = prepare(engine, state, revision)
    state = Path(state).resolve(strict=True)
    scripts = str(pinned / "scripts")
    if scripts in sys.path:
        sys.path.remove(scripts)
    sys.path.insert(0, scripts)
    if tool == "proposal":
        return run_proposal(state, revision, arguments)
    # Old workflow revisions are immutable. Do not patch their source on disk.
    # Their common module accepts a dynamic REPO_ROOT on import, but the pinned
    # engine root itself is correct thanks to the books/ symlink.
    book = importlib.import_module("book")
    book.REPO_ROOT = pinned

    # Parallel dispatch and optional claim base revisions must reference the
    # book-state Git HEAD, never the public workflow commit.
    def state_head(_engine_root: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(state), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    for name in ("claim_cli", "review_cli", "status_cli", "epub_cli"):
        module = importlib.import_module("workflow." + name)
        if hasattr(module, "_git_head"):
            module._git_head = state_head
    if tool == "book":
        return int(book.main(arguments) or 0)
    if tool == "corpus":
        corpus = importlib.import_module("corpus")
        return int(corpus.main(arguments) or 0)
    raise ValueError("unsupported pinned CLI command")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("tool", choices=("book", "corpus", "proposal"))
    args, remainder = parser.parse_known_args(argv)
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    if not remainder:
        parser.error("expected canonical CLI subcommand")
    return run(args.engine, args.state, args.revision, args.tool, remainder)


if __name__ == "__main__":
    raise SystemExit(main())
