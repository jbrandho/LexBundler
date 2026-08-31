"""Meaningful persistence operations for corpus sources and evidence."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from lexbundler.domain.corpus import (
    Asset,
    AssetBinding,
    AssetLocation,
    CorpusSource,
    JsonObject,
    ProcessingRun,
    SourceUnit,
)


class CorpusStore(Protocol):
    def create_source(
        self,
        *,
        name: str,
        source_kind: str | None,
        language_tag: str | None,
        external_id: str | None,
        metadata: JsonObject,
        created_by_run_id: int | None,
        created_at: datetime,
    ) -> CorpusSource: ...

    def get_source(self, source_id: int) -> CorpusSource: ...

    def list_sources(self) -> list[CorpusSource]: ...

    def create_source_unit(
        self,
        *,
        source_id: int,
        parent_id: int | None,
        kind: str,
        label: str,
        sequence: int | None,
        external_id: str | None,
        metadata: JsonObject,
        created_by_run_id: int | None,
        confidence: float | None,
        created_at: datetime,
    ) -> SourceUnit: ...

    def get_source_unit(self, unit_id: int) -> SourceUnit: ...

    def list_source_units(self, source_id: int) -> list[SourceUnit]: ...

    def register_asset(
        self,
        *,
        sha256: str,
        byte_size: int,
        asset_kind: str | None,
        mime_type: str | None,
        location_kind: str,
        location: str,
        created_by_run_id: int | None,
        observed_at: datetime,
    ) -> Asset: ...

    def get_asset(self, asset_id: int) -> Asset: ...

    def find_asset_by_sha256(self, sha256: str) -> Asset | None: ...

    def list_asset_locations(self, asset_id: int) -> list[AssetLocation]: ...

    def create_asset_binding(
        self,
        *,
        source_id: int,
        source_unit_id: int | None,
        asset_id: int,
        role: str | None,
        assignment_method: str | None,
        confidence: float | None,
        processing_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> AssetBinding: ...

    def list_asset_bindings(self, source_id: int) -> list[AssetBinding]: ...

    def start_processing_run(
        self,
        *,
        run_uuid: UUID,
        process_type: str,
        tool_name: str | None,
        tool_version: str | None,
        parameters: JsonObject,
        started_at: datetime,
    ) -> ProcessingRun: ...

    def finish_processing_run(
        self, run_id: int, *, status: str, completed_at: datetime
    ) -> ProcessingRun: ...

    def get_processing_run(self, run_id: int) -> ProcessingRun: ...

