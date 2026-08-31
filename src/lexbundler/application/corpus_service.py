"""Application operations for generic sources, assets, and provenance."""

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lexbundler.domain.corpus import (
    Asset,
    AssetBinding,
    AssetLocation,
    CorpusSource,
    JsonObject,
    ProcessingRun,
    SourceUnit,
)
from lexbundler.domain.errors import (
    AssetFileError,
    InvalidCorpusDataError,
    NoOpenProjectError,
)
from lexbundler.persistence.corpus_store import CorpusStore

HASH_CHUNK_SIZE = 1024 * 1024
PROCESSING_STATUSES = frozenset({"running", "succeeded", "failed", "cancelled"})


class CorpusService:
    """Coordinate corpus operations against the currently attached project store."""

    def __init__(self) -> None:
        self._store: CorpusStore | None = None

    def _attach(self, store: CorpusStore) -> None:
        self._store = store

    def _detach(self) -> None:
        self._store = None

    def create_source(
        self,
        name: str,
        *,
        source_kind: str | None = None,
        language_tag: str | None = None,
        external_id: str | None = None,
        metadata: JsonObject | None = None,
        created_by_run_id: int | None = None,
    ) -> CorpusSource:
        name = name.strip()
        if not name:
            raise InvalidCorpusDataError("Source name must not be empty.")
        return self._require_store().create_source(
            name=name,
            source_kind=_optional_text(source_kind),
            language_tag=_optional_text(language_tag),
            external_id=_optional_text(external_id),
            metadata=_json_object(metadata),
            created_by_run_id=created_by_run_id,
            created_at=_now(),
        )

    def get_source(self, source_id: int) -> CorpusSource:
        return self._require_store().get_source(source_id)

    def list_sources(self) -> list[CorpusSource]:
        return self._require_store().list_sources()

    def create_source_unit(
        self,
        source_id: int,
        *,
        kind: str,
        label: str,
        parent_id: int | None = None,
        sequence: int | None = None,
        external_id: str | None = None,
        metadata: JsonObject | None = None,
        created_by_run_id: int | None = None,
        confidence: float | None = None,
    ) -> SourceUnit:
        kind = kind.strip()
        label = label.strip()
        if not kind or not label:
            raise InvalidCorpusDataError("Source-unit kind and label are required.")
        _validate_confidence(confidence)
        return self._require_store().create_source_unit(
            source_id=source_id,
            parent_id=parent_id,
            kind=kind,
            label=label,
            sequence=sequence,
            external_id=_optional_text(external_id),
            metadata=_json_object(metadata),
            created_by_run_id=created_by_run_id,
            confidence=confidence,
            created_at=_now(),
        )

    def get_source_unit(self, unit_id: int) -> SourceUnit:
        return self._require_store().get_source_unit(unit_id)

    def list_source_units(self, source_id: int) -> list[SourceUnit]:
        return self._require_store().list_source_units(source_id)

    def register_local_asset(
        self,
        path: Path,
        *,
        asset_kind: str | None = None,
        mime_type: str | None = None,
        created_by_run_id: int | None = None,
    ) -> Asset:
        store = self._require_store()
        file_path = Path(path)
        digest, byte_size = _hash_local_file(file_path)
        guessed_mime = mime_type or mimetypes.guess_type(file_path.name)[0]
        guessed_kind = asset_kind or _kind_from_mime(guessed_mime)
        return store.register_asset(
            sha256=digest,
            byte_size=byte_size,
            asset_kind=_optional_text(guessed_kind),
            mime_type=_optional_text(guessed_mime),
            location_kind="filesystem",
            location=str(file_path.resolve()),
            created_by_run_id=created_by_run_id,
            observed_at=_now(),
        )

    def get_asset(self, asset_id: int) -> Asset:
        return self._require_store().get_asset(asset_id)

    def find_asset_by_sha256(self, sha256: str) -> Asset | None:
        return self._require_store().find_asset_by_sha256(sha256.lower())

    def list_asset_locations(self, asset_id: int) -> list[AssetLocation]:
        return self._require_store().list_asset_locations(asset_id)

    def bind_asset_to_source(
        self,
        source_id: int,
        asset_id: int,
        *,
        role: str | None = None,
        assignment_method: str | None = None,
        confidence: float | None = None,
        processing_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> AssetBinding:
        return self._bind_asset(
            source_id,
            None,
            asset_id,
            role=role,
            assignment_method=assignment_method,
            confidence=confidence,
            processing_run_id=processing_run_id,
            metadata=metadata,
        )

    def bind_asset_to_source_unit(
        self,
        source_id: int,
        source_unit_id: int,
        asset_id: int,
        *,
        role: str | None = None,
        assignment_method: str | None = None,
        confidence: float | None = None,
        processing_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> AssetBinding:
        return self._bind_asset(
            source_id,
            source_unit_id,
            asset_id,
            role=role,
            assignment_method=assignment_method,
            confidence=confidence,
            processing_run_id=processing_run_id,
            metadata=metadata,
        )

    def list_asset_bindings(self, source_id: int) -> list[AssetBinding]:
        return self._require_store().list_asset_bindings(source_id)

    def start_processing_run(
        self,
        process_type: str,
        *,
        tool_name: str | None = None,
        tool_version: str | None = None,
        parameters: JsonObject | None = None,
    ) -> ProcessingRun:
        process_type = process_type.strip()
        if not process_type:
            raise InvalidCorpusDataError("Processing-run type must not be empty.")
        return self._require_store().start_processing_run(
            run_uuid=uuid4(),
            process_type=process_type,
            tool_name=_optional_text(tool_name),
            tool_version=_optional_text(tool_version),
            parameters=_json_object(parameters),
            started_at=_now(),
        )

    def finish_processing_run(
        self, run_id: int, *, status: str
    ) -> ProcessingRun:
        if status not in PROCESSING_STATUSES - {"running"}:
            raise InvalidCorpusDataError(
                "Finished run status must be succeeded, failed, or cancelled."
            )
        return self._require_store().finish_processing_run(
            run_id, status=status, completed_at=_now()
        )

    def get_processing_run(self, run_id: int) -> ProcessingRun:
        return self._require_store().get_processing_run(run_id)

    def _bind_asset(
        self,
        source_id: int,
        source_unit_id: int | None,
        asset_id: int,
        *,
        role: str | None,
        assignment_method: str | None,
        confidence: float | None,
        processing_run_id: int | None,
        metadata: JsonObject | None,
    ) -> AssetBinding:
        _validate_confidence(confidence)
        return self._require_store().create_asset_binding(
            source_id=source_id,
            source_unit_id=source_unit_id,
            asset_id=asset_id,
            role=_optional_text(role),
            assignment_method=_optional_text(assignment_method),
            confidence=confidence,
            processing_run_id=processing_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def _require_store(self) -> CorpusStore:
        if self._store is None:
            raise NoOpenProjectError("Open a project before accessing corpus data.")
        return self._store


def _hash_local_file(path: Path) -> tuple[str, int]:
    if not path.is_file():
        raise AssetFileError(f"Asset path is not a regular file: {path}")
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with path.open("rb") as source_file:
            while chunk := source_file.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
                byte_size += len(chunk)
    except OSError as error:
        raise AssetFileError(f"Could not read asset file: {path}") from error
    return digest.hexdigest(), byte_size


def _kind_from_mime(mime_type: str | None) -> str | None:
    if mime_type is None:
        return None
    category = mime_type.partition("/")[0]
    if category in {"audio", "image", "text", "video"}:
        return category
    if mime_type == "application/pdf":
        return "document"
    return None


def _validate_confidence(confidence: float | None) -> None:
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise InvalidCorpusDataError("Confidence must be between 0.0 and 1.0.")


def _json_object(value: object) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise InvalidCorpusDataError("Metadata must be a JSON object.")
    return dict(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidCorpusDataError("Expected text metadata.")
    return value.strip() or None


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
