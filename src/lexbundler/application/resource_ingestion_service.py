"""Application workflow for atomically creating a logical corpus resource."""

import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from uuid import uuid4

from lexbundler.application.corpus_service import CorpusService, _hash_local_file
from lexbundler.application.project_explorer_service import ResourceIdentity
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.application.transcript_import_service import _nonempty_line_ranges
from lexbundler.domain.errors import NoOpenProjectError, ResourceIngestionError
from lexbundler.persistence.resource_ingestion_store import (
    AssetAttachmentPlan, PreparedAsset, PreparedText, ResourceIngestionPlan,
    ResourceIngestionStore,
)


class ResourceType(Enum):
    AUDIO_TRANSCRIPT = "audio_transcript"
    AUDIO_ONLY = "audio_only"
    TEXT_ONLY = "text_only"

    @property
    def label(self) -> str:
        return {
            self.AUDIO_TRANSCRIPT: "Audio + Transcript",
            self.AUDIO_ONLY: "Audio Only",
            self.TEXT_ONLY: "Text Only",
        }[self]


class TextProvenance(Enum):
    AUTHORITATIVE = "authoritative"
    MACHINE_UNREVIEWED = "machine_unreviewed"

    @property
    def label(self) -> str:
        return {
            self.AUTHORITATIVE: "Authoritative / trusted",
            self.MACHINE_UNREVIEWED: "Machine-generated / needs review",
        }[self]


class AssetAttachmentType(Enum):
    AUDIO = "audio"
    TEXT = "text"

    @property
    def label(self) -> str:
        return "Audio" if self is self.AUDIO else "Text / Transcript"


@dataclass(frozen=True, slots=True)
class ResourceIngestionRequest:
    resource_type: ResourceType
    resource_name: str
    existing_source_id: int | None = None
    new_source_name: str | None = None
    existing_parent_unit_id: int | None = None
    new_parent_labels: tuple[str, ...] = ()
    audio_path: Path | None = None
    text_path: Path | None = None
    text_provenance: TextProvenance = TextProvenance.AUTHORITATIVE
    language_tag: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceIngestionResult:
    resource: ResourceIdentity
    asset_ids: tuple[int, ...]
    text_representation_id: int | None
    processing_run_id: int


@dataclass(frozen=True, slots=True)
class AssetAttachmentRequest:
    resource: ResourceIdentity
    asset_type: AssetAttachmentType
    path: Path
    text_provenance: TextProvenance = TextProvenance.AUTHORITATIVE
    language_tag: str | None = None


@dataclass(frozen=True, slots=True)
class AssetAttachmentResult:
    resource: ResourceIdentity
    asset_id: int
    text_representation_id: int | None
    processing_run_id: int


class ResourceIngestionService:
    def __init__(self, corpus: CorpusService,
                 text_segments: TextSegmentService) -> None:
        self._corpus = corpus
        self._text_segments = text_segments
        self._store: ResourceIngestionStore | None = None

    def _attach(self, store: ResourceIngestionStore) -> None:
        self._store = store

    def _detach(self) -> None:
        self._store = None

    def ingest(self, request: ResourceIngestionRequest) -> ResourceIngestionResult:
        store = self._require_store()
        resource_name = _required_name(request.resource_name, "Resource name")
        new_source_name = _optional_name(request.new_source_name)
        if (request.existing_source_id is None) == (new_source_name is None):
            raise ResourceIngestionError(
                "Choose an existing source or provide one new source name."
            )
        parent_labels = tuple(
            _required_name(label, "Parent path component")
            for label in request.new_parent_labels
        )
        self._validate_hierarchy(
            request.existing_source_id, new_source_name,
            request.existing_parent_unit_id, parent_labels, resource_name,
        )

        needs_audio = request.resource_type in {
            ResourceType.AUDIO_TRANSCRIPT, ResourceType.AUDIO_ONLY,
        }
        needs_text = request.resource_type in {
            ResourceType.AUDIO_TRANSCRIPT, ResourceType.TEXT_ONLY,
        }
        if needs_audio != (request.audio_path is not None):
            message = (
                "An audio file is required."
                if needs_audio else "This resource type does not accept audio."
            )
            raise ResourceIngestionError(message)
        if needs_text != (request.text_path is not None):
            message = (
                "A text file is required."
                if needs_text else "This resource type does not accept text."
            )
            raise ResourceIngestionError(message)

        assets: list[PreparedAsset] = []
        if request.audio_path is not None:
            assets.append(_prepare_asset(request.audio_path, "audio", "source_audio"))
        prepared_text = None
        if request.text_path is not None:
            text_asset, prepared_text = _prepare_text(
                request.text_path, request.text_provenance
            )
            assets.append(text_asset)

        plan = ResourceIngestionPlan(
            existing_source_id=request.existing_source_id,
            new_source_name=new_source_name,
            existing_parent_unit_id=request.existing_parent_unit_id,
            new_parent_labels=parent_labels,
            resource_name=resource_name,
            resource_type=request.resource_type.value,
            language_tag=_optional_name(request.language_tag),
            assets=tuple(assets),
            text=prepared_text,
            run_uuid=uuid4(),
            timestamp=datetime.now(UTC).replace(microsecond=0),
        )
        stored = store.ingest_resource(plan)
        return ResourceIngestionResult(
            ResourceIdentity(stored.source_id, stored.resource_unit_id),
            stored.asset_ids, stored.text_representation_id,
            stored.processing_run_id,
        )

    def add_asset_to_resource(
        self, request: AssetAttachmentRequest
    ) -> AssetAttachmentResult:
        store = self._require_store()
        source = next((item for item in self._corpus.list_sources()
                       if item.id == request.resource.source_id), None)
        if source is None or request.resource.source_unit_id is None:
            raise ResourceIngestionError("The selected resource no longer exists.")
        unit = next((item for item in self._corpus.list_source_units(source.id)
                     if item.id == request.resource.source_unit_id), None)
        if unit is None:
            raise ResourceIngestionError("The selected resource no longer exists.")
        text = None
        if request.asset_type is AssetAttachmentType.AUDIO:
            asset = _prepare_asset(request.path, "audio", "source_audio")
        else:
            asset, text = _prepare_text(request.path, request.text_provenance)
        stored = store.attach_asset(AssetAttachmentPlan(
            source.id, unit.id, asset, text, _optional_name(request.language_tag), uuid4(),
            datetime.now(UTC).replace(microsecond=0),
        ))
        return AssetAttachmentResult(
            request.resource, stored.asset_id, stored.text_representation_id,
            stored.processing_run_id,
        )

    def list_container_units(self, source_id: int):
        """Return hierarchy units that are not themselves logical resources."""
        evidence_unit_ids = {
            binding.source_unit_id
            for binding in self._corpus.list_asset_bindings(source_id)
            if binding.source_unit_id is not None
        }
        evidence_unit_ids.update(
            item.source_unit_id
            for item in self._text_segments.list_text_representations(source_id)
            if item.source_unit_id is not None
        )
        evidence_unit_ids.update(
            item.source_unit_id
            for item in self._text_segments.list_segment_layers(source_id)
            if item.source_unit_id is not None
        )
        units = self._corpus.list_source_units(source_id)
        excluded = evidence_unit_ids | {
            unit.id for unit in units if unit.kind == "resource"
        }
        changed = True
        while changed:
            descendants = {
                unit.id for unit in units if unit.parent_id in excluded
            }
            changed = not descendants.issubset(excluded)
            excluded.update(descendants)
        return tuple(unit for unit in units if unit.id not in excluded)

    def _validate_hierarchy(
        self, source_id: int | None, new_source_name: str | None,
        parent_id: int | None, new_labels: tuple[str, ...], resource_name: str,
    ) -> None:
        sources = self._corpus.list_sources()
        if new_source_name is not None:
            if any(source.name == new_source_name for source in sources):
                raise ResourceIngestionError(
                    f'A source named "{new_source_name}" already exists.'
                )
            if parent_id is not None:
                raise ResourceIngestionError(
                    "A new source cannot reuse a parent from another source."
                )
            return
        source = next((item for item in sources if item.id == source_id), None)
        if source is None:
            raise ResourceIngestionError("The selected source no longer exists.")
        units = self._corpus.list_source_units(source.id)
        by_id = {unit.id: unit for unit in units}
        if parent_id is not None and parent_id not in by_id:
            raise ResourceIngestionError(
                "The selected parent does not belong to the selected source."
            )
        cursor = parent_id
        for label in (*new_labels, resource_name):
            if any(unit.parent_id == cursor and unit.label == label for unit in units):
                raise ResourceIngestionError(
                    f'A unit named "{label}" already exists at that hierarchy level.'
                )
            # New path components do not have IDs until the transaction; later
            # duplicate checks are therefore handled inside the store as well.
            cursor = -1

    def _require_store(self) -> ResourceIngestionStore:
        if self._store is None:
            raise NoOpenProjectError("Open a project before adding a resource.")
        return self._store


def _prepare_asset(path: Path, kind: str, role: str,
                   mime_type: str | None = None) -> PreparedAsset:
    file_path = Path(path)
    try:
        digest, byte_size = _hash_local_file(file_path)
    except Exception as error:
        raise ResourceIngestionError(str(error)) from error
    return PreparedAsset(
        str(file_path.resolve()), digest, byte_size, kind,
        mime_type or mimetypes.guess_type(file_path.name)[0], role,
    )


def _prepare_text(
    path: Path, provenance: TextProvenance
) -> tuple[PreparedAsset, PreparedText]:
    text_path = Path(path)
    suffix = text_path.suffix.lower()
    if suffix == ".json":
        raise ResourceIngestionError(
            "JSON files are structured data and cannot be imported as plain "
            "transcript text. Use the appropriate processing/import workflow instead."
        )
    if suffix != ".txt":
        raise ResourceIngestionError(
            "Generic text ingestion currently supports plain UTF-8 .txt files only."
        )
    try:
        content = text_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ResourceIngestionError(
            f"Could not read exact UTF-8 text: {text_path}"
        ) from error
    authoritative = provenance is TextProvenance.AUTHORITATIVE
    role = "authoritative_transcript" if authoritative else "machine_transcript"
    return (
        _prepare_asset(text_path, "text", role, "text/plain"),
        PreparedText(
            content=content,
            representation_kind=(
                "authoritative_source" if authoritative else "machine_transcript"
            ),
            authority="source" if authoritative else "machine_unreviewed",
            binding_role=role,
            spans=_nonempty_line_ranges(content),
        ),
    )


def _required_name(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ResourceIngestionError(f"{label} is required.")
    return normalized


def _optional_name(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None
