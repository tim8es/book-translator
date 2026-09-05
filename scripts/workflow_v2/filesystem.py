"""Filesystem storage backend for Workflow v2 durable state."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from .storage import (
    InvalidStoragePath,
    StorageAlreadyExists,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
    StoredValue,
)

try:  # pragma: no cover - platform selection is exercised by CI host.
    import fcntl
except ImportError:  # pragma: no cover - Windows only.
    fcntl = None

try:  # pragma: no cover - platform selection is exercised by CI host.
    import msvcrt
except ImportError:  # pragma: no cover - POSIX only.
    msvcrt = None


def _revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _acquire_file_lock(handle: BinaryIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:  # pragma: no cover - Windows only.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    raise StorageError("filesystem compare-and-swap locking is unsupported on this platform")


def _release_file_lock(handle: BinaryIO) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows only.
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    raise StorageError("filesystem compare-and-swap locking is unsupported on this platform")


class FilesystemStorage:
    """Store logical workflow paths under one filesystem root."""

    _thread_registry_guard = threading.Lock()
    _thread_locks: dict[str, threading.Lock] = {}

    def __init__(self, root: Path):
        self.root = Path(root).resolve(strict=False)

    def _resolve(self, path: str, *, allow_empty: bool = False) -> Path:
        if not isinstance(path, str):
            raise InvalidStoragePath("storage path must be a string")
        if path == "":
            if allow_empty:
                return self.root
            raise InvalidStoragePath("storage path must not be empty")
        if path.startswith("/") or "\\" in path:
            raise InvalidStoragePath(f"unsafe storage path: {path!r}")

        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise InvalidStoragePath(f"unsafe storage path: {path!r}")

        target = self.root.joinpath(*parts).resolve(strict=False)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise InvalidStoragePath(f"storage path escapes root: {path!r}") from exc
        return target

    @classmethod
    def _thread_lock_for(cls, target: Path) -> threading.Lock:
        key = os.path.normcase(str(target))
        with cls._thread_registry_guard:
            lock = cls._thread_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._thread_locks[key] = lock
            return lock

    @staticmethod
    def _lock_file_for(target: Path) -> Path:
        normalized = os.path.normcase(str(target)).encode("utf-8")
        key = hashlib.sha256(normalized).hexdigest()
        user_id = getattr(os, "getuid", lambda: 0)()
        lock_root = Path(tempfile.gettempdir()) / f"book-translator-workflow-v2-locks-{user_id}"
        lock_root.mkdir(parents=True, exist_ok=True)
        return lock_root / f"{key}.lock"

    @contextmanager
    def _mutation_lock(self, path: str) -> Iterator[None]:
        target = self._resolve(path)
        thread_lock = self._thread_lock_for(target)
        with thread_lock:
            lock_path = self._lock_file_for(target)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            with os.fdopen(fd, "r+b", buffering=0) as handle:
                _acquire_file_lock(handle)
                try:
                    yield
                finally:
                    _release_file_lock(handle)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def read(self, path: str) -> StoredValue:
        target = self._resolve(path)
        if not target.is_file():
            raise StorageNotFound(path)
        try:
            content = target.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFound(path) from exc
        return StoredValue(content=content, version=_revision(content))

    def create_if_absent(self, path: str, content: bytes) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError as exc:
            raise StorageAlreadyExists(path) from exc

        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise

        self._fsync_directory(target.parent)
        return _revision(content)

    def write_if_version(self, path: str, content: bytes, expected_version: str) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._mutation_lock(path):
            current = self.read(path)
            if current.version != expected_version:
                raise StorageVersionConflict(
                    f"{path}: expected revision {expected_version}, current revision {current.version}"
                )

            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())

                latest = self.read(path)
                if latest.version != expected_version:
                    raise StorageVersionConflict(
                        f"{path}: expected revision {expected_version}, current revision {latest.version}"
                    )

                os.replace(temp_path, target)
                temp_path = None
                self._fsync_directory(target.parent)
                return _revision(content)
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass

    def delete_if_version(self, path: str, expected_version: str) -> None:
        target = self._resolve(path)
        with self._mutation_lock(path):
            current = self.read(path)
            if current.version != expected_version:
                raise StorageVersionConflict(
                    f"{path}: expected revision {expected_version}, current revision {current.version}"
                )

            latest = self.read(path)
            if latest.version != expected_version:
                raise StorageVersionConflict(
                    f"{path}: expected revision {expected_version}, current revision {latest.version}"
                )
            try:
                target.unlink()
            except FileNotFoundError as exc:
                raise StorageNotFound(path) from exc
            self._fsync_directory(target.parent)

    def list(self, prefix: str = "") -> list[str]:
        target = self._resolve(prefix, allow_empty=True)
        if not target.exists():
            return []

        if target.is_file():
            return [target.relative_to(self.root).as_posix()]

        results: list[str] = []
        for candidate in target.rglob("*"):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(self.root)
            except ValueError:
                continue
            results.append(candidate.relative_to(self.root).as_posix())
        return sorted(results)
