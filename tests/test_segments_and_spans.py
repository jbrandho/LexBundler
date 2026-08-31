from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import (
    CorpusIntegrityError,
    InvalidCorpusDataError,
    InvalidSpanError,
)
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project_service = ProjectService(SQLiteProjectStoreFactory())
    project_service.create_project(tmp_path / "segments.lexbundler", name="Segments")
    return project_service


def test_segment_layers_and_arbitrary_hierarchy(service: ProjectService) -> None:
    source = service.corpus.create_source("Source")
    unit = service.corpus.create_source_unit(source.id, kind="part", label="Part")
    run = service.corpus.start_processing_run("segmentation")
    manual = service.text_segments.create_segment_layer(
        source.id,
        source_unit_id=unit.id,
        name="Reviewed dialogue turns",
        layer_kind="anything-free-form",
        language_tag="cmn-Hans-CN",
        metadata={"reviewer": "synthetic"},
        created_by_run_id=run.id,
    )
    other = service.text_segments.create_segment_layer(
        source.id, name="Other", layer_kind="other"
    )
    root = service.text_segments.create_segment(
        manual.id, kind="dialogue", confidence=0.0, created_by_run_id=run.id
    )
    turn = service.text_segments.create_segment(
        manual.id,
        parent_id=root.id,
        kind="speaker_turn",
        label="Turn A",
        sequence=1,
        external_id="turn-1",
    )
    utterance = service.text_segments.create_segment(
        manual.id, parent_id=turn.id, kind="utterance", confidence=1.0
    )

    assert service.text_segments.get_segment_layer(manual.id) == manual
    assert manual.created_by_run_id == run.id
    assert root.created_by_run_id == run.id
    assert service.text_segments.list_segment_layers(source.id) == [manual, other]
    assert service.text_segments.get_segment(utterance.id) == utterance
    assert utterance.label is None
    assert utterance.sequence is None
    assert utterance.external_id is None
    assert {segment.id for segment in service.text_segments.list_segments(manual.id)} == {
        root.id,
        turn.id,
        utterance.id,
    }
    assert service.text_segments.list_segment_text_spans(utterance.id) == []
    assert service.text_segments.list_segment_media_spans(utterance.id) == []


def test_cross_source_layer_unit_and_cross_layer_parent_are_rejected(
    service: ProjectService,
) -> None:
    first = service.corpus.create_source("First")
    second = service.corpus.create_source("Second")
    other_unit = service.corpus.create_source_unit(second.id, kind="unit", label="Unit")
    with pytest.raises(CorpusIntegrityError):
        service.text_segments.create_segment_layer(
            first.id,
            source_unit_id=other_unit.id,
            name="Invalid",
            layer_kind="manual",
        )

    first_layer = service.text_segments.create_segment_layer(
        first.id, name="First", layer_kind="manual"
    )
    second_layer = service.text_segments.create_segment_layer(
        first.id, name="Second", layer_kind="manual"
    )
    parent = service.text_segments.create_segment(first_layer.id, kind="parent")
    with pytest.raises(CorpusIntegrityError):
        service.text_segments.create_segment(
            second_layer.id, parent_id=parent.id, kind="child"
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_segment_confidence_outside_range_is_rejected(
    service: ProjectService, confidence: float
) -> None:
    source = service.corpus.create_source("Source")
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    with pytest.raises(InvalidCorpusDataError):
        service.text_segments.create_segment(
            layer.id, kind="unit", confidence=confidence
        )


def test_unicode_half_open_text_spans_resolve_as_python_slices(
    service: ProjectService,
) -> None:
    source = service.corpus.create_source("Source")
    chinese = service.text_segments.create_text_representation(
        source.id, representation_kind="reviewed", content="你好世界"
    )
    emoji = service.text_segments.create_text_representation(
        source.id, representation_kind="alternate", content="😀汉字"
    )
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="utterances"
    )
    segment = service.text_segments.create_segment(layer.id, kind="utterance")
    chinese_span = service.text_segments.add_segment_text_span(
        segment.id, chinese.id, 1, 3, role="primary", confidence=0.0
    )
    emoji_span = service.text_segments.add_segment_text_span(
        segment.id, emoji.id, 0, 1, role="emoji-code-point", confidence=1.0
    )
    whole = service.text_segments.add_segment_text_span(
        segment.id, chinese.id, 0, len(chinese.content)
    )
    overlapping = service.text_segments.add_segment_text_span(
        segment.id, chinese.id, 2, 4
    )

    assert service.text_segments.resolve_text_span(chinese_span.id) == "好世"
    assert service.text_segments.resolve_text_span(emoji_span.id) == "😀"
    assert service.text_segments.resolve_text_span(whole.id) == "你好世界"
    assert service.text_segments.resolve_text_span(overlapping.id) == "世界"
    assert len("😀".encode("utf-8")) == 4
    assert len(service.text_segments.list_segment_text_spans(segment.id)) == 4


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 1), (0, 0), (2, 1), (True, 1), (0, False), (0.0, 1)],
)
def test_invalid_text_ranges_are_rejected(
    service: ProjectService, start: object, end: object
) -> None:
    source = service.corpus.create_source("Source")
    representation = service.text_segments.create_text_representation(
        source.id, representation_kind="text", content="abc"
    )
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    segment = service.text_segments.create_segment(layer.id, kind="segment")
    with pytest.raises(InvalidSpanError):
        service.text_segments.add_segment_text_span(
            segment.id, representation.id, start, end  # type: ignore[arg-type]
        )


def test_out_of_bounds_and_cross_source_text_spans_are_rejected(
    service: ProjectService,
) -> None:
    first = service.corpus.create_source("First")
    second = service.corpus.create_source("Second")
    text = service.text_segments.create_text_representation(
        first.id, representation_kind="text", content="abc"
    )
    other_text = service.text_segments.create_text_representation(
        second.id, representation_kind="text", content="xyz"
    )
    layer = service.text_segments.create_segment_layer(
        first.id, name="Layer", layer_kind="manual"
    )
    segment = service.text_segments.create_segment(layer.id, kind="segment")

    with pytest.raises(InvalidSpanError):
        service.text_segments.add_segment_text_span(segment.id, text.id, 0, 4)
    with pytest.raises(CorpusIntegrityError):
        service.text_segments.add_segment_text_span(segment.id, other_text.id, 0, 1)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_text_span_confidence_outside_range_is_rejected(
    service: ProjectService, confidence: float
) -> None:
    source = service.corpus.create_source("Source")
    text = service.text_segments.create_text_representation(
        source.id, representation_kind="text", content="abc"
    )
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    segment = service.text_segments.create_segment(layer.id, kind="segment")
    with pytest.raises(InvalidCorpusDataError):
        service.text_segments.add_segment_text_span(
            segment.id, text.id, 0, 1, confidence=confidence
        )


def test_same_range_in_different_layers_and_span_provenance_are_allowed(
    service: ProjectService,
) -> None:
    source = service.corpus.create_source("Source")
    text = service.text_segments.create_text_representation(
        source.id, representation_kind="text", content="abcdef"
    )
    first_layer = service.text_segments.create_segment_layer(
        source.id, name="First", layer_kind="one"
    )
    second_layer = service.text_segments.create_segment_layer(
        source.id, name="Second", layer_kind="two"
    )
    first = service.text_segments.create_segment(first_layer.id, kind="unit")
    second = service.text_segments.create_segment(second_layer.id, kind="unit")
    run = service.corpus.start_processing_run("text_alignment")
    first_span = service.text_segments.add_segment_text_span(
        first.id, text.id, 1, 4, created_by_run_id=run.id
    )
    second_span = service.text_segments.add_segment_text_span(
        second.id, text.id, 1, 4
    )

    assert first_span.id != second_span.id
    assert first_span.created_by_run_id == run.id
