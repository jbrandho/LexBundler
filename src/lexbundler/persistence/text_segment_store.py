"""Persistence boundary for text representations and analytical segments."""

from datetime import datetime
from typing import Protocol

from lexbundler.domain.corpus import JsonObject
from lexbundler.domain.text_segments import (
    Segment,
    SegmentLayer,
    SegmentMediaSpan,
    SegmentSpeaker,
    SegmentTextSpan,
    Speaker,
    TextRepresentation,
)


class TextSegmentStore(Protocol):
    def create_text_representation(
        self,
        *,
        source_id: int,
        source_unit_id: int | None,
        representation_kind: str,
        language_tag: str | None,
        content: str,
        source_asset_id: int | None,
        derived_from_id: int | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> TextRepresentation: ...

    def get_text_representation(self, representation_id: int) -> TextRepresentation: ...

    def list_text_representations(self, source_id: int) -> list[TextRepresentation]: ...

    def create_segment_layer(
        self,
        *,
        source_id: int,
        source_unit_id: int | None,
        name: str,
        layer_kind: str,
        language_tag: str | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentLayer: ...

    def get_segment_layer(self, layer_id: int) -> SegmentLayer: ...

    def list_segment_layers(self, source_id: int) -> list[SegmentLayer]: ...

    def create_segment(
        self,
        *,
        layer_id: int,
        parent_id: int | None,
        kind: str,
        label: str | None,
        sequence: int | None,
        external_id: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> Segment: ...

    def get_segment(self, segment_id: int) -> Segment: ...

    def list_segments(self, layer_id: int) -> list[Segment]: ...

    def add_segment_text_span(
        self,
        *,
        segment_id: int,
        text_representation_id: int,
        start_offset: int,
        end_offset: int,
        role: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentTextSpan: ...

    def get_segment_text_span(self, span_id: int) -> SegmentTextSpan: ...

    def list_segment_text_spans(self, segment_id: int) -> list[SegmentTextSpan]: ...

    def add_segment_media_span(
        self,
        *,
        segment_id: int,
        asset_id: int,
        start_ms: int,
        end_ms: int,
        role: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentMediaSpan: ...

    def get_segment_media_span(self, span_id: int) -> SegmentMediaSpan: ...

    def list_segment_media_spans(self, segment_id: int) -> list[SegmentMediaSpan]: ...

    def create_speaker(
        self,
        *,
        source_id: int,
        name: str,
        external_id: str | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> Speaker: ...

    def get_speaker(self, speaker_id: int) -> Speaker: ...

    def list_speakers(self, source_id: int) -> list[Speaker]: ...

    def add_segment_speaker(
        self,
        *,
        segment_id: int,
        speaker_id: int,
        role: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentSpeaker: ...

    def list_segment_speakers(self, segment_id: int) -> list[SegmentSpeaker]: ...

