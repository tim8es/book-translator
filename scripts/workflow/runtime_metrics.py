"""Optional, in-memory measurements of actual runtime operations.

Never reads workflow files, calls GitHub, or writes telemetry. Callers explicitly
export snapshots when useful; no AI-generated counts are accepted as evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from time import perf_counter_ns
from typing import Callable, Iterator

from .storage import StorageBackend, StoredValue


class RuntimeMetrics:
    """Thread-safe counters with a monotonic clock and no persisted state."""

    def __init__(self, *, clock_ns: Callable[[], int] = perf_counter_ns):
        self._clock_ns = clock_ns
        self._lock = Lock()
        self._stats: dict[str, dict[str, int]] = {}

    def _entry(self, operation: str) -> dict[str, int]:
        return self._stats.setdefault(operation, {
            "calls": 0, "failures": 0, "elapsed_ns": 0,
            "bytes_read": 0, "bytes_written": 0,
        })

    @contextmanager
    def measure(self, operation: str) -> Iterator[None]:
        """Count an attempted operation, including one that raises an error."""
        started = self._clock_ns()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = max(0, self._clock_ns() - started)
            with self._lock:
                entry = self._entry(operation)
                entry["calls"] += 1
                entry["failures"] += int(failed)
                entry["elapsed_ns"] += elapsed

    def add_bytes(self, operation: str, *, read: int = 0, written: int = 0) -> None:
        """Record payload sizes without storing payloads, paths, or credentials."""
        with self._lock:
            entry = self._entry(operation)
            entry["bytes_read"] += read
            entry["bytes_written"] += written

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Return a JSON-serializable, detached snapshot; no I/O is performed."""
        with self._lock:
            return {key: dict(value) for key, value in sorted(self._stats.items())}


class MeasuredStorage:
    """StorageBackend proxy that observes work without altering backend semantics."""

    def __init__(self, backend: StorageBackend, metrics: RuntimeMetrics):
        self.backend = backend
        self.metrics = metrics

    def read(self, path: str) -> StoredValue:
        with self.metrics.measure("storage.read"):
            result = self.backend.read(path)
            self.metrics.add_bytes("storage.read", read=len(result.content))
            return result

    def list(self, prefix: str = "") -> list[str]:
        with self.metrics.measure("storage.list"):
            return self.backend.list(prefix)

    def create_if_absent(self, path: str, content: bytes) -> str:
        with self.metrics.measure("storage.create"):
            result = self.backend.create_if_absent(path, content)
            self.metrics.add_bytes("storage.create", written=len(content))
            return result

    def write_if_version(self, path: str, content: bytes, expected_version: str) -> str:
        with self.metrics.measure("storage.update"):
            result = self.backend.write_if_version(path, content, expected_version)
            self.metrics.add_bytes("storage.update", written=len(content))
            return result

    def delete_if_version(self, path: str, expected_version: str) -> None:
        with self.metrics.measure("storage.delete"):
            self.backend.delete_if_version(path, expected_version)
