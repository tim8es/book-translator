"""Public internal API for Workflow v2 state infrastructure."""

from .claims import (
    ActiveClaim,
    ClaimAuditError,
    ClaimConflict,
    ClaimError,
    ClaimLifecycleResult,
    ClaimManager,
    ClaimOwnershipError,
    ClaimRollbackError,
    InvalidClaimSelector,
    canonical_unit_id,
    resolve_selector,
)
from .filesystem import FilesystemStorage
from .migration_journal import (
    MIGRATION_PATH,
    MigrationJournalError,
    load_migration_journal,
    serialize_migration_journal,
    validate_migration_journal,
)
from .migrations import (
    MigratedDocument,
    MigrationCompatibilityError,
    MigrationConflict,
    MigrationError,
    detect_schema_version,
    migrate_document,
)
from .repository import LoadedDocument, RepositoryError, WorkflowStateRepository
from .reviews import (
    AcceptReviewResult,
    ReviewClaimError,
    ReviewConflict,
    ReviewError,
    ReviewEvidenceError,
    ReviewLedgerManager,
    ReviewRecordResult,
    ReviewResolution,
)
from .schemas import (
    ParsedDocument,
    SchemaError,
    SchemaKind,
    UnsupportedSchemaVersion,
    parse_document,
)
from .source_schema import install_source_schema_extensions
from .storage import (
    InvalidStoragePath,
    StorageAlreadyExists,
    StorageBackend,
    StorageError,
    StorageNotFound,
    StorageVersionConflict,
    StoredValue,
)
from .text_patch import TextPatchError, TextPatchResult, patch_text

install_source_schema_extensions()

__all__ = [
    "AcceptReviewResult",
    "ActiveClaim",
    "ClaimAuditError",
    "ClaimConflict",
    "ClaimError",
    "ClaimLifecycleResult",
    "ClaimManager",
    "ClaimOwnershipError",
    "ClaimRollbackError",
    "FilesystemStorage",
    "InvalidClaimSelector",
    "InvalidStoragePath",
    "LoadedDocument",
    "MIGRATION_PATH",
    "MigratedDocument",
    "MigrationCompatibilityError",
    "MigrationConflict",
    "MigrationError",
    "MigrationJournalError",
    "ParsedDocument",
    "RepositoryError",
    "ReviewClaimError",
    "ReviewConflict",
    "ReviewError",
    "ReviewEvidenceError",
    "ReviewLedgerManager",
    "ReviewRecordResult",
    "ReviewResolution",
    "SchemaError",
    "SchemaKind",
    "StorageAlreadyExists",
    "StorageBackend",
    "StorageError",
    "StorageNotFound",
    "StorageVersionConflict",
    "StoredValue",
    "TextPatchError",
    "TextPatchResult",
    "UnsupportedSchemaVersion",
    "WorkflowStateRepository",
    "canonical_unit_id",
    "detect_schema_version",
    "load_migration_journal",
    "migrate_document",
    "parse_document",
    "patch_text",
    "resolve_selector",
    "serialize_migration_journal",
    "validate_migration_journal",
]
