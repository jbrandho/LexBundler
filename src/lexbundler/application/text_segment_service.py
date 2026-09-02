"""Application operations for immutable text and analytical segmentation."""

from datetime import UTC, datetime

from lexbundler.domain.corpus import JsonObject
from lexbundler.domain.errors import (
    InvalidCorpusDataError,
    InvalidSpanError,
    NoOpenProjectError,
)
from lexbundler.domain.text_segments import (
    AlignmentGraph,
    AlignmentGraphSpec,
    FlatSegmentGraphSpec,
    Segment,
    SegmentLayer,
    SegmentMediaSpan,
    SegmentSpeaker,
    SegmentTextSpan,
    Speaker,
    TextRepresentation,
    TextOnlySegmentGraphSpec,
    TextSegmentGraph,
)
from lexbundler.persistence.text_segment_store import TextSegmentStore


class TextSegmentService:
    """Coordinate text and segmentation operations for the open project."""

    def create_text_only_segment_graph(
        self, spec: TextOnlySegmentGraphSpec
    ) -> TextSegmentGraph:
        _required_text(spec.representation_kind, "Representation kind")
        _required_text(spec.layer_name, "Layer name")
        _required_text(spec.layer_kind, "Layer kind")
        _required_text(spec.segment_kind, "Segment kind")
        if not isinstance(spec.content, str):
            raise InvalidCorpusDataError("Text content must be a Unicode string.")
        for item in spec.segments:
            _validate_range(item.start_offset, item.end_offset, "Text offsets")
            if item.end_offset > len(spec.content):
                raise InvalidSpanError("Text span ends beyond the graph representation.")
        return self._require_store().create_text_only_segment_graph(
            spec, created_at=_now()
        )

    def create_alignment_graph(self, spec: AlignmentGraphSpec) -> AlignmentGraph:
        for layer in spec.layers:
            _required_text(layer.name, "Layer name")
            _required_text(layer.layer_kind, "Layer kind")
            _required_text(layer.segment_kind, "Segment kind")
            for item in layer.segments:
                if not isinstance(item.label, str):
                    raise InvalidCorpusDataError("Alignment label must be text.")
                _validate_range(item.start_ms, item.end_ms, "Media offsets")
                if (item.text_start is None) != (item.text_end is None):
                    raise InvalidSpanError("Text span start and end must both be present.")
                if item.text_start is not None and item.text_end is not None:
                    _validate_range(item.text_start, item.text_end, "Text offsets")
        return self._require_store().create_alignment_graph(spec, created_at=_now())

    def __init__(self) -> None:
        self._store: TextSegmentStore | None = None

    def _attach(self, store: TextSegmentStore) -> None:
        self._store = store

    def _detach(self) -> None:
        self._store = None

    def create_flat_segment_graph(
        self, spec: FlatSegmentGraphSpec
    ) -> TextSegmentGraph:
        """Atomically create one representation and an ordered flat segment layer."""
        _required_text(spec.representation_kind, "Representation kind")
        _required_text(spec.layer_name, "Layer name")
        _required_text(spec.layer_kind, "Layer kind")
        _required_text(spec.segment_kind, "Segment kind")
        if not isinstance(spec.content, str):
            raise InvalidCorpusDataError("Text content must be a Unicode string.")
        for item in spec.segments:
            _validate_range(item.media_start_ms, item.media_end_ms, "Media offsets")
            if (item.text_start is None) != (item.text_end is None):
                raise InvalidSpanError("Text span start and end must both be present.")
            if item.text_start is not None and item.text_end is not None:
                _validate_range(item.text_start, item.text_end, "Text offsets")
                if item.text_end > len(spec.content):
                    raise InvalidSpanError(
                        "Text span ends beyond the graph representation."
                    )
        return self._require_store().create_flat_segment_graph(
            spec, created_at=_now()
        )

    def create_text_representation(
        self,
        source_id: int,
        *,
        representation_kind: str,
        content: str,
        source_unit_id: int | None = None,
        language_tag: str | None = None,
        source_asset_id: int | None = None,
        derived_from_id: int | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> TextRepresentation:
        kind = _required_text(representation_kind, "Representation kind")
        if not isinstance(content, str):
            raise InvalidCorpusDataError("Text content must be a Unicode string.")
        return self._require_store().create_text_representation(
            source_id=source_id,
            source_unit_id=source_unit_id,
            representation_kind=kind,
            language_tag=_optional_text(language_tag),
            content=content,
            source_asset_id=source_asset_id,
            derived_from_id=derived_from_id,
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def get_text_representation(self, representation_id: int) -> TextRepresentation:
        return self._require_store().get_text_representation(representation_id)

    def list_text_representations(self, source_id: int) -> list[TextRepresentation]:
        return self._require_store().list_text_representations(source_id)

    def create_segment_layer(
        self,
        source_id: int,
        *,
        name: str,
        layer_kind: str,
        source_unit_id: int | None = None,
        language_tag: str | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> SegmentLayer:
        return self._require_store().create_segment_layer(
            source_id=source_id,
            source_unit_id=source_unit_id,
            name=_required_text(name, "Layer name"),
            layer_kind=_required_text(layer_kind, "Layer kind"),
            language_tag=_optional_text(language_tag),
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def get_segment_layer(self, layer_id: int) -> SegmentLayer:
        return self._require_store().get_segment_layer(layer_id)

    def list_segment_layers(self, source_id: int) -> list[SegmentLayer]:
        return self._require_store().list_segment_layers(source_id)

    def create_segment(
        self,
        layer_id: int,
        *,
        kind: str,
        parent_id: int | None = None,
        label: str | None = None,
        sequence: int | None = None,
        external_id: str | None = None,
        confidence: float | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> Segment:
        _validate_confidence(confidence)
        return self._require_store().create_segment(
            layer_id=layer_id,
            parent_id=parent_id,
            kind=_required_text(kind, "Segment kind"),
            label=_optional_text(label),
            sequence=sequence,
            external_id=_optional_text(external_id),
            confidence=confidence,
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def get_segment(self, segment_id: int) -> Segment:
        return self._require_store().get_segment(segment_id)

    def list_segments(self, layer_id: int) -> list[Segment]:
        return self._require_store().list_segments(layer_id)

    def add_segment_text_span(
        self,
        segment_id: int,
        text_representation_id: int,
        start_offset: int,
        end_offset: int,
        *,
        role: str | None = None,
        confidence: float | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> SegmentTextSpan:
        _validate_range(start_offset, end_offset, "Text offsets")
        _validate_confidence(confidence)
        return self._require_store().add_segment_text_span(
            segment_id=segment_id,
            text_representation_id=text_representation_id,
            start_offset=start_offset,
            end_offset=end_offset,
            role=_optional_text(role),
            confidence=confidence,
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def get_segment_text_span(self, span_id: int) -> SegmentTextSpan:
        return self._require_store().get_segment_text_span(span_id)

    def list_segment_text_spans(self, segment_id: int) -> list[SegmentTextSpan]:
        return self._require_store().list_segment_text_spans(segment_id)

    def resolve_text_span(self, span_id: int) -> str:
        span = self.get_segment_text_span(span_id)
        representation = self.get_text_representation(span.text_representation_id)
        return representation.content[span.start_offset : span.end_offset]

    def add_segment_media_span(
        self,
        segment_id: int,
        asset_id: int,
        start_ms: int,
        end_ms: int,
        *,
        role: str | None = None,
        confidence: float | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> SegmentMediaSpan:
        _validate_range(start_ms, end_ms, "Media offsets")
        _validate_confidence(confidence)
        return self._require_store().add_segment_media_span(
            segment_id=segment_id,
            asset_id=asset_id,
            start_ms=start_ms,
            end_ms=end_ms,
            role=_optional_text(role),
            confidence=confidence,
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def get_segment_media_span(self, span_id: int) -> SegmentMediaSpan:
        return self._require_store().get_segment_media_span(span_id)

    def list_segment_media_spans(self, segment_id: int) -> list[SegmentMediaSpan]:
        return self._require_store().list_segment_media_spans(segment_id)

    def create_speaker(
        self,
        source_id: int,
        *,
        name: str,
        external_id: str | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> Speaker:
        return self._require_store().create_speaker(
            source_id=source_id,
            name=_required_text(name, "Speaker name"),
            external_id=_optional_text(external_id),
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def get_speaker(self, speaker_id: int) -> Speaker:
        return self._require_store().get_speaker(speaker_id)

    def list_speakers(self, source_id: int) -> list[Speaker]:
        return self._require_store().list_speakers(source_id)

    def add_segment_speaker(
        self,
        segment_id: int,
        speaker_id: int,
        *,
        role: str | None = None,
        confidence: float | None = None,
        created_by_run_id: int | None = None,
        metadata: JsonObject | None = None,
    ) -> SegmentSpeaker:
        _validate_confidence(confidence)
        return self._require_store().add_segment_speaker(
            segment_id=segment_id,
            speaker_id=speaker_id,
            role=_optional_text(role),
            confidence=confidence,
            created_by_run_id=created_by_run_id,
            metadata=_json_object(metadata),
            created_at=_now(),
        )

    def list_segment_speakers(self, segment_id: int) -> list[SegmentSpeaker]:
        return self._require_store().list_segment_speakers(segment_id)

    def _require_store(self) -> TextSegmentStore:
        if self._store is None:
            raise NoOpenProjectError("Open a project before accessing text or segments.")
        return self._store


def _validate_range(start: int, end: int, name: str) -> None:
    if type(start) is not int or type(end) is not int:
        raise InvalidSpanError(f"{name} must be integers, not booleans or floats.")
    if start < 0 or end <= start:
        raise InvalidSpanError(
            f"{name} must form a non-empty half-open range with a nonnegative start."
        )


def _validate_confidence(confidence: float | None) -> None:
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise InvalidCorpusDataError("Confidence must be between 0.0 and 1.0.")


def _required_text(value: object, name: str) -> str:
    result = _optional_text(value)
    if result is None:
        raise InvalidCorpusDataError(f"{name} must not be empty.")
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidCorpusDataError("Expected text metadata.")
    return value.strip() or None


def _json_object(value: object) -> JsonObject:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise InvalidCorpusDataError("Metadata must be a JSON object.")
    return dict(value)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
