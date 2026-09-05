"""Filesystem storage backend for Workflow v2 durable state."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from .storage import (
    InvalidStoragePath,
    StorageAlreadyExists,
    StorageNotFound,
    StorageVersionConflict,
    StoredValue,
)


def _revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FilesystemStorage:
    """Store logical workflow paths under one filesystem root."""

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
        current = self.read(path)
        if current.version != expected_version:
            raise StorageVersionConflict(
                f"{path}: expected revision {expected_version}, current revision {current.version}"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
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

            # Re-check immediately before replacement so ordinary stale writers
            # cannot overwrite a newer durable revision. Cross-process claim/lease
            # coordination is intentionally completed by Workflow v2 issue #8.
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
