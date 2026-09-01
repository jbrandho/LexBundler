"""Immutable text representations and analytical segmentation domain models."""

from dataclasses import dataclass
from datetime import datetime

from lexbundler.domain.corpus import JsonObject


@dataclass(frozen=True, slots=True)
class TextRepresentation:
    id: int
    source_id: int
    source_unit_id: int | None
    representation_kind: str
    language_tag: str | None
    content: str
    source_asset_id: int | None
    derived_from_id: int | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SegmentLayer:
    id: int
    source_id: int
    source_unit_id: int | None
    name: str
    layer_kind: str
    language_tag: str | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Segment:
    id: int
    layer_id: int
    parent_id: int | None
    kind: str
    label: str | None
    sequence: int | None
    external_id: str | None
    confidence: float | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SegmentTextSpan:
    id: int
    segment_id: int
    text_representation_id: int
    start_offset: int
    end_offset: int
    role: str | None
    confidence: float | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SegmentMediaSpan:
    id: int
    segment_id: int
    asset_id: int
    start_ms: int
    end_ms: int
    role: str | None
    confidence: float | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Speaker:
    id: int
    source_id: int
    name: str
    external_id: str | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SegmentSpeaker:
    id: int
    segment_id: int
    speaker_id: int
    role: str | None
    confidence: float | None
    created_by_run_id: int | None
    metadata: JsonObject
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FlatSegmentSpec:
    """One ordered item in an atomic, flat text/media segmentation graph."""

    sequence: int
    external_id: str | None
    text_start: int | None
    text_end: int | None
    media_asset_id: int
    media_start_ms: int
    media_end_ms: int


@dataclass(frozen=True, slots=True)
class FlatSegmentGraphSpec:
    source_id: int
    source_unit_id: int | None
    representation_kind: str
    language_tag: str | None
    content: str
    source_asset_id: int | None
    created_by_run_id: int | None
    representation_metadata: JsonObject
    layer_name: str
    layer_kind: str
    layer_metadata: JsonObject
    segment_kind: str
    text_span_role: str | None
    media_span_role: str | None
    segments: tuple[FlatSegmentSpec, ...]


@dataclass(frozen=True, slots=True)
class TextSegmentGraph:
    representation: TextRepresentation
    layer: SegmentLayer
    segments: tuple[Segment, ...]
    text_spans: tuple[SegmentTextSpan, ...]
    media_spans: tuple[SegmentMediaSpan, ...]

