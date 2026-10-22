"""Optional process-local JSONL measurements, independent of AI agent reports.

Set BOOK_TRANSLATOR_METRICS_FILE to an external log path. Logging is disabled
otherwise and does not modify the book's authoritative state.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone


ENVIRONMENT_VARIABLE = "BOOK_TRANSLATOR_METRICS_FILE"


def enabled() -> bool:
    return bool(os.environ.get(ENVIRONMENT_VARIABLE))


def emit(kind: str, **fields: object) -> None:
    """Append one small event; measurement failure must not alter workflow outcome."""
    path = os.environ.get(ENVIRONMENT_VARIABLE)
    if not path:
        return
    event = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        **fields,
    }
    data = (json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, data) != len(data):
                raise OSError("short metrics write")
        finally:
            os.close(fd)
    except OSError:
        # One append per event, no extra GitHub requests or mutations to book state.
        print("WARNING: Book Translator metrics event could not be recorded", file=sys.stderr)
