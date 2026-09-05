"""Public internal API for Workflow v2 state infrastructure."""

from .claims import ClaimError, InvalidClaimSelector, canonical_unit_id, resolve_selector
from .filesystem import FilesystemStorage
from .repository import LoadedDocument, RepositoryError, WorkflowStateRepository
from .schemas import (
    ParsedDocument,
    SchemaError,
    SchemaKind,
    UnsupportedSchemaVersion,
    parse_document,
)
from .storage import (
    InvalidStoragePath,
    StorageAlreadyExists,
    StorageBackend,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
    StoredValue,
)

__all__ = [
    "ClaimError",
    "FilesystemStorage",
    "InvalidClaimSelector",
    "InvalidStoragePath",
    "LoadedDocument",
    "ParsedDocument",
    "RepositoryError",
    "SchemaError",
    "SchemaKind",
    "StorageAlreadyExists",
    "StorageBackend",
    "StorageError",
    "StorageNotFound",
    "StorageVersionConflict",
    "StoredValue",
    "UnsupportedSchemaVersion",
    "WorkflowStateRepository",
    "canonical_unit_id",
    "parse_document",
    "resolve_selector",
]
