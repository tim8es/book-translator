"""Read-only Workflow v2 status and resume resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .claims import ClaimManager, canonical_unit_id
from .repository import WorkflowStateRepository
from .reviews import REVIEW_EVIDENCE_VERSION, ReviewEvidenceError, ReviewLedgerManager
from .schemas import SchemaKind
from .storage import StorageError, StorageNotFound


STATUS_SCHEMA_VERSION = 1
_REVIEW_STATES = (
    "pass",
    "stale",
    "missing",
    "corrections_required",
    "untranslated",
)
_LIFECYCLE_STATES = ("pending", "extracted", "translated", "reviewed")


class StatusError(RuntimeError):
    """Workflow status cannot be resolved safely."""


class StatusResolver:
    """Resolve repository-authoritative status without mutating durable state."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        *,
        artifact_reader: Callable[[str], bytes],
    ):
        self.repository = repository
        self._artifact_reader = artifact_reader

    def status(
        self,
        *,
        structural_errors: Sequence[str] = (),
        corpus: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata_doc = self.repository.read(
            "metadata.json",
            SchemaKind.METADATA,
            allow_legacy=True,
        )
        progress_doc = self.repository.read(
            "progress.json",
            SchemaKind.PROGRESS,
            allow_legacy=True,
        )
        metadata = metadata_doc.data
        progress = progress_doc.data

        workflow = metadata.get("workflow")
        workflow_revision = None
        if isinstance(workflow, Mapping):
            resolved = workflow.get("resolved_revision")
            requested = workflow.get("requested_ref")
            if isinstance(resolved, str) and resolved.strip():
                workflow_revision = resolved
            elif isinstance(requested, str) and requested.strip():
                workflow_revision = requested

        errors = [str(error) for error in structural_errors if str(error)]
        if workflow_revision is None:
            errors.append("workflow revision is unavailable")

        corpus_data = dict(corpus or {"state": "unsealed"})
        corpus_state = corpus_data.get("state")
        if corpus_state not in {"verified", "unsealed", "invalid"}:
            errors.append(f"unsupported corpus state: {corpus_state!r}")
        elif corpus_state == "invalid":
            detail = corpus_data.get("error")
            errors.append(str(detail) if detail else "source corpus integrity is invalid")

        lifecycle = {state: 0 for state in _LIFECYCLE_STATES}
        chapters = progress.get("chapters")
        if not isinstance(chapters, list):
            raise StatusError("progress state must contain a chapters array")
        for chapter in chapters:
            if not isinstance(chapter, Mapping):
                raise StatusError("progress chapter must be an object")
            state = chapter.get("status")
            if state not in lifecycle:
                raise StatusError(f"unsupported chapter lifecycle state: {state!r}")
            lifecycle[state] += 1

        review_states_by_number: dict[int, str] = {}
        review_ledger_revision: str | None = None
        review_mode = "legacy_lifecycle"
        if isinstance(workflow, Mapping) and workflow.get("review_evidence") == REVIEW_EVIDENCE_VERSION:
            review_mode = REVIEW_EVIDENCE_VERSION
            try:
                review_ledger_revision = self.repository.read(
                    "review-ledger.json",
                    SchemaKind.REVIEW_LEDGER,
                ).version
                review_manager = ReviewLedgerManager(
                    self.repository,
                    artifact_reader=self._artifact_reader,
                )
                for resolution in review_manager.resolve_all(progress, metadata):
                    review_states_by_number[resolution.chapter_number] = resolution.state
            except (ReviewEvidenceError, StorageError, StorageNotFound) as exc:
                errors.append(f"review evidence is unavailable or invalid: {exc}")
        else:
            for chapter in chapters:
                number = chapter.get("number")
                lifecycle_state = chapter.get("status")
                if lifecycle_state == "reviewed":
                    review_states_by_number[number] = "pass"
                elif lifecycle_state == "translated":
                    review_states_by_number[number] = "missing"
                else:
                    review_states_by_number[number] = "untranslated"

        reviews = {state: 0 for state in _REVIEW_STATES}
        units: list[dict[str, Any]] = []
        for chapter in sorted(chapters, key=lambda item: item["number"]):
            number = chapter["number"]
            review_state = review_states_by_number.get(number, "missing")
            if review_state not in reviews:
                errors.append(
                    f"chapter {number} has unsupported review state {review_state!r}"
                )
                review_state = "missing"
            reviews[review_state] += 1

            lifecycle_state = chapter["status"]
            if lifecycle_state == "reviewed" and review_state != "pass":
                errors.append(
                    f"chapter {number} is reviewed without current PASS evidence; review state={review_state}"
                )
            if lifecycle_state == "translated" and review_state == "untranslated":
                errors.append(
                    f"chapter {number} is translated but its translation artifact is unavailable"
                )

            units.append(
                {
                    "unit_id": canonical_unit_id(number),
                    "chapter_number": number,
                    "lifecycle": lifecycle_state,
                    "review": review_state,
                    "source_path": chapter["source_path"],
                    "translation_path": chapter["translation_path"],
                }
            )

        try:
            active_claims = ClaimManager(self.repository).list_active()
        except Exception as exc:
            errors.append(f"claims are unavailable or invalid: {exc}")
            active_claims = []
        claims = [
            {
                "unit_id": claim.data["unit_id"],
                "role": claim.data["role"],
                "session_id": claim.data["session_id"],
                "expires_at": claim.data["expires_at"],
            }
            for claim in active_claims
        ]

        revisions = {
            "metadata": metadata_doc.version,
            "progress": progress_doc.version,
            "review_ledger": review_ledger_revision,
        }
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "book_slug": progress.get("book_slug"),
            "workflow_revision": workflow_revision,
            "review_mode": review_mode,
            "valid": not errors,
            "errors": errors,
            "lifecycle": lifecycle,
            "reviews": reviews,
            "claims": claims,
            "corpus": corpus_data,
            "units": units,
            "state_revisions": revisions,
        }

    def resume(self, status: Mapping[str, Any]) -> dict[str, Any]:
        """Select the next read-only orchestration operation from a status snapshot."""

        if not isinstance(status, Mapping):
            raise StatusError("status snapshot must be an object")
        if not status.get("valid"):
            return {
                "schema_version": STATUS_SCHEMA_VERSION,
                "operation": "blocked",
                "reason": "preflight_failed",
                "errors": list(status.get("errors") or ()),
                "context": self._context("blocked", None, status),
            }

        claims_by_unit = {
            claim["unit_id"]: claim
            for claim in status.get("claims", ())
            if isinstance(claim, Mapping) and isinstance(claim.get("unit_id"), str)
        }
        units = status.get("units")
        if not isinstance(units, list):
            raise StatusError("status snapshot must contain units")

        for unit in units:
            if not isinstance(unit, Mapping):
                raise StatusError("status unit must be an object")
            lifecycle = unit.get("lifecycle")
            review = unit.get("review")
            if lifecycle == "reviewed":
                continue

            unit_id = unit.get("unit_id")
            if unit_id in claims_by_unit:
                return {
                    "schema_version": STATUS_SCHEMA_VERSION,
                    "operation": "blocked",
                    "reason": "unit_claimed",
                    "unit_id": unit_id,
                    "chapter_number": unit.get("chapter_number"),
                    "claim": dict(claims_by_unit[unit_id]),
                    "errors": [f"{unit_id} is already claimed"],
                    "context": self._context("blocked", unit, status),
                }

            if lifecycle == "extracted":
                operation = "translate"
            elif lifecycle == "translated" and review == "pass":
                operation = "accept_review"
            elif lifecycle == "translated" and review == "corrections_required":
                operation = "correct_translation"
            elif lifecycle == "translated" and review in {"missing", "stale"}:
                operation = "review"
            elif lifecycle == "pending":
                return {
                    "schema_version": STATUS_SCHEMA_VERSION,
                    "operation": "blocked",
                    "reason": "chapter_not_extracted",
                    "unit_id": unit_id,
                    "chapter_number": unit.get("chapter_number"),
                    "errors": [f"{unit_id} has not been extracted"],
                    "context": self._context("blocked", unit, status),
                }
            else:
                return {
                    "schema_version": STATUS_SCHEMA_VERSION,
                    "operation": "blocked",
                    "reason": "unresolvable_unit_state",
                    "unit_id": unit_id,
                    "chapter_number": unit.get("chapter_number"),
                    "errors": [
                        f"{unit_id} cannot resume from lifecycle={lifecycle!r}, review={review!r}"
                    ],
                    "context": self._context("blocked", unit, status),
                }

            return {
                "schema_version": STATUS_SCHEMA_VERSION,
                "operation": operation,
                "unit_id": unit_id,
                "chapter_number": unit.get("chapter_number"),
                "context": self._context(operation, unit, status),
            }

        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "operation": "complete",
            "context": self._context("complete", None, status),
        }

    @staticmethod
    def _context(
        operation: str,
        unit: Mapping[str, Any] | None,
        status: Mapping[str, Any],
    ) -> dict[str, Any]:
        if operation in {"translate", "correct_translation"}:
            role = "translator"
            contracts = ["AGENTS.md", "docs/TRANSLATION.md"]
        elif operation == "review":
            role = "reviewer"
            contracts = ["AGENTS.md", "docs/TRANSLATION.md"]
        else:
            role = "orchestrator"
            contracts = ["AGENTS.md", "docs/ORCHESTRATION.md"]

        files = ["metadata.json", "progress.json"]
        if role in {"translator", "reviewer"}:
            files.extend(["glossary.md", "style-guide.md"])
        if unit is not None:
            source_path = unit.get("source_path")
            translation_path = unit.get("translation_path")
            if isinstance(source_path, str) and source_path:
                files.append(source_path)
            if operation in {"review", "correct_translation", "accept_review"}:
                if isinstance(translation_path, str) and translation_path:
                    files.append(translation_path)
        if operation in {"accept_review", "complete"}:
            files.append("review-ledger.json")

        return {
            "role": role,
            "profile": role,
            "workflow_revision": status.get("workflow_revision"),
            "contracts": contracts,
            "files": files,
            "state_revisions": dict(status.get("state_revisions") or {}),
        }
