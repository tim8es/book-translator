"""Public internal API for the Book Translator workflow runtime."""

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
from .github_api import (
    GitHubApiClient,
    GitHubApiError,
    GitHubFile,
    GitHubMutation,
    GitHubRestClient,
    GitHubTree,
    GitHubTreeEntry,
)
from .github_storage import GitHubStorage
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
    "GitHubApiClient",
    "GitHubApiError",
    "GitHubFile",
    "GitHubMutation",
    "GitHubRestClient",
    "GitHubStorage",
    "GitHubTree",
    "GitHubTreeEntry",
    "InvalidClaimSelector",
    "InvalidStoragePath",
    "LoadedDocument",
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
    "parse_document",
    "patch_text",
    "resolve_selector",
]
