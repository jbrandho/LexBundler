"""Language-neutral corpus source, asset, and provenance domain models."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class CorpusSource:
    id: int
    name: str
    source_kind: str | None
    language_tag: str | None
    external_id: str | None
    metadata: JsonObject
    created_by_run_id: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceUnit:
    id: int
    source_id: int
    parent_id: int | None
    kind: str
    label: str
    sequence: int | None
    external_id: str | None
    metadata: JsonObject
    created_by_run_id: int | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Asset:
    id: int
    sha256: str
    byte_size: int
    asset_kind: str | None
    mime_type: str | None
    created_by_run_id: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AssetLocation:
    id: int
    asset_id: int
    location_kind: str
    location: str
    created_by_run_id: int | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AssetBinding:
    id: int
    source_id: int
    source_unit_id: int | None
    asset_id: int
    role: str | None
    assignment_method: str | None
    confidence: float | None
    processing_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    id: int
    run_uuid: UUID
    process_type: str
    tool_name: str | None
    tool_version: str | None
    parameters: JsonObject
    status: str
    started_at: datetime
    completed_at: datetime | None

