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
    project_service.create_project(tmp_path / "media.lexbundler", name="Media")
    return project_service


def test_media_spans_use_integer_milliseconds_and_allow_overlap(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Source")
    layer = service.text_segments.create_segment_layer(
        source.id, name="Temporal", layer_kind="manual"
    )
    first_segment = service.text_segments.create_segment(layer.id, kind="turn")
    second_segment = service.text_segments.create_segment(layer.id, kind="turn")
    image_path = tmp_path / "temporal.png"
    image_path.write_bytes(b"synthetic non-audio asset")
    asset = service.corpus.register_local_asset(image_path)
    run = service.corpus.start_processing_run("media_alignment")

    first = service.text_segments.add_segment_media_span(
        first_segment.id,
        asset.id,
        0,
        1200,
        role="free-form-source",
        confidence=0.0,
        created_by_run_id=run.id,
    )
    overlap = service.text_segments.add_segment_media_span(
        first_segment.id, asset.id, 800, 1600, confidence=1.0
    )
    other_segment = service.text_segments.add_segment_media_span(
        second_segment.id, asset.id, 700, 900
    )

    assert asset.asset_kind == "image"
    assert first.start_ms == 0 and first.end_ms == 1200
    assert first.created_by_run_id == run.id
    assert service.text_segments.get_segment_media_span(overlap.id) == overlap
    assert service.text_segments.list_segment_media_spans(first_segment.id) == [
        first,
        overlap,
    ]
    assert other_segment.id != overlap.id


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 1), (0, 0), (2, 1), (True, 2), (0, False), (0.0, 1)],
)
def test_invalid_media_ranges_are_rejected(
    service: ProjectService, tmp_path: Path, start: object, end: object
) -> None:
    source = service.corpus.create_source("Source")
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    segment = service.text_segments.create_segment(layer.id, kind="segment")
    path = tmp_path / "asset.bin"
    path.write_bytes(b"asset")
    asset = service.corpus.register_local_asset(path)
    with pytest.raises(InvalidSpanError):
        service.text_segments.add_segment_media_span(
            segment.id, asset.id, start, end  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_media_span_confidence_outside_range_is_rejected(
    service: ProjectService, tmp_path: Path, confidence: float
) -> None:
    source = service.corpus.create_source("Source")
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    segment = service.text_segments.create_segment(layer.id, kind="segment")
    path = tmp_path / "asset.bin"
    path.write_bytes(b"asset")
    asset = service.corpus.register_local_asset(path)
    with pytest.raises(InvalidCorpusDataError):
        service.text_segments.add_segment_media_span(
            segment.id, asset.id, 0, 1, confidence=confidence
        )


def test_source_scoped_speakers_and_many_to_many_assignments(
    service: ProjectService,
) -> None:
    first_source = service.corpus.create_source("First")
    second_source = service.corpus.create_source("Second")
    run = service.corpus.start_processing_run("speaker_detection")
    first_named = service.text_segments.create_speaker(
        first_source.id, name="小王", created_by_run_id=run.id
    )
    duplicate_named = service.text_segments.create_speaker(first_source.id, name="小王")
    other_source_named = service.text_segments.create_speaker(
        second_source.id, name="小王"
    )
    second_speaker = service.text_segments.create_speaker(
        first_source.id, name="Narrator", external_id="narrator-1"
    )
    layer = service.text_segments.create_segment_layer(
        first_source.id, name="Turns", layer_kind="speaker_turn"
    )
    segment = service.text_segments.create_segment(layer.id, kind="turn")

    assert service.text_segments.list_segment_speakers(segment.id) == []
    assignment_run = service.corpus.start_processing_run("speaker_assignment")
    primary = service.text_segments.add_segment_speaker(
        segment.id,
        first_named.id,
        role="primary-custom",
        confidence=0.0,
        created_by_run_id=assignment_run.id,
    )
    overlapping = service.text_segments.add_segment_speaker(
        segment.id, second_speaker.id, role="overlapping", confidence=1.0
    )
    competing = service.text_segments.add_segment_speaker(
        segment.id, first_named.id, role="machine-alternate"
    )

    assert len({first_named.id, duplicate_named.id, other_source_named.id}) == 3
    assert service.text_segments.get_speaker(first_named.id) == first_named
    assert service.text_segments.list_speakers(first_source.id) == [
        first_named,
        duplicate_named,
        second_speaker,
    ]
    assert first_named.created_by_run_id == run.id
    assert primary.created_by_run_id == assignment_run.id
    assert service.text_segments.list_segment_speakers(segment.id) == [
        primary,
        overlapping,
        competing,
    ]
    with pytest.raises(CorpusIntegrityError):
        service.text_segments.add_segment_speaker(segment.id, other_source_named.id)


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_segment_speaker_confidence_outside_range_is_rejected(
    service: ProjectService, confidence: float
) -> None:
    source = service.corpus.create_source("Source")
    speaker = service.text_segments.create_speaker(source.id, name="Speaker")
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    segment = service.text_segments.create_segment(layer.id, kind="turn")
    with pytest.raises(InvalidCorpusDataError):
        service.text_segments.add_segment_speaker(
            segment.id, speaker.id, confidence=confidence
        )
