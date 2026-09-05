"""Focused persistence contract for one atomic logical-resource ingestion."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PreparedAsset:
    path: str
    sha256: str
    byte_size: int
    asset_kind: str
    mime_type: str | None
    role: str


@dataclass(frozen=True, slots=True)
class PreparedText:
    content: str
    representation_kind: str
    authority: str
    binding_role: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class ResourceIngestionPlan:
    existing_source_id: int | None
    new_source_name: str | None
    existing_parent_unit_id: int | None
    new_parent_labels: tuple[str, ...]
    resource_name: str
    resource_type: str
    language_tag: str | None
    assets: tuple[PreparedAsset, ...]
    text: PreparedText | None
    run_uuid: UUID
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class StoredResourceIngestion:
    source_id: int
    resource_unit_id: int
    asset_ids: tuple[int, ...]
    text_representation_id: int | None
    processing_run_id: int


@dataclass(frozen=True, slots=True)
class AssetAttachmentPlan:
    source_id: int
    resource_unit_id: int
    asset: PreparedAsset
    text: PreparedText | None
    language_tag: str | None
    run_uuid: UUID
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class StoredAssetAttachment:
    asset_id: int
    text_representation_id: int | None
    processing_run_id: int


class ResourceIngestionStore(Protocol):
    def ingest_resource(
        self, plan: ResourceIngestionPlan
    ) -> StoredResourceIngestion: ...

    def attach_asset(
        self, plan: AssetAttachmentPlan
    ) -> StoredAssetAttachment: ...
