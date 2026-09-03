import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from lexbundler.application.pedagogical_review_service import (
    APPROVED_MEDIA_ROLE,
    REVIEW_LAYER_KIND,
    REVIEW_SEGMENT_KIND,
    REVIEW_TEXT_ROLE,
    PedagogicalReviewRequest,
)
from lexbundler.domain.errors import PedagogicalReviewError
from lexbundler.domain.text_segments import (
    AlignmentGraphSpec, AlignmentLayerSpec, AlignmentSegmentSpec,
)
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.application.project_service import ProjectService
import lexbundler.persistence.sqlite.text_segment_store as sqlite_text_store


def _context(tmp_path: Path, content: str = "你好\n你好"):
    project_path = tmp_path / "review.lexbundler"
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(project_path, name="Review")
    source = project.corpus.create_source("Dialogue", language_tag="zh")
    transcript_path = tmp_path / "transcript.txt"
    transcript_path.write_text(content, encoding="utf-8")
    graph = project.transcript_imports.import_utf8(
        transcript_path, source_id=source.id, language_tag="zh"
    ).graph
    audio_path = tmp_path / "source audio.wav"
    audio_path.write_bytes(b"source audio")
    audio = project.corpus.register_local_asset(audio_path, asset_kind="audio")
    return project, project_path, source, graph, audio, audio_path


def _request(project, graph, audio, index=0, start=500, end=900, edited=False):
    segment = graph.segments[index]
    span = project.text_segments.list_segment_text_spans(segment.id)[0]
    return PedagogicalReviewRequest(
        segment.id, span.id, audio.id, start, end, None,
        start + 50, end - 50, edited,
    )


def test_approval_persists_exact_authoritative_and_original_media_spans(tmp_path: Path) -> None:
    project, project_path, source, graph, audio, _audio_path = _context(tmp_path)
    transcript_before = project.text_segments.list_segments(graph.layer.id)
    mfa_run = project.corpus.start_processing_run("import", tool_name="LexBundler")
    mfa = project.text_segments.create_alignment_graph(AlignmentGraphSpec(
        source.id, None, graph.representation.id, audio.id, "zh", mfa_run.id,
        "authoritative_alignment", "aligned_source",
        (AlignmentLayerSpec(
            "MFA word alignment", "forced_alignment", "alignment_word",
            {"tier": "words"},
            (AlignmentSegmentSpec(0, "你好", 550, 850, 3, 5),),
        ),),
    ))
    project.corpus.finish_processing_run(mfa_run.id, status="succeeded")
    mfa_before = (
        project.text_segments.list_segments(mfa.layers[0].id),
        project.text_segments.list_segment_text_spans(mfa.segments[0].id),
        project.text_segments.list_segment_media_spans(mfa.segments[0].id),
    )
    request = replace(
        _request(project, graph, audio, index=1, start=1200, end=1800, edited=True),
        alignment_layer_id=mfa.layers[0].id,
        mfa_speech_start_ms=550,
        mfa_speech_end_ms=850,
    )
    files_before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    approval = project.pedagogical_reviews.approve(
        request
    )

    assert approval.graph.layers[0].layer_kind == REVIEW_LAYER_KIND
    assert approval.reviewed_segment.kind == REVIEW_SEGMENT_KIND
    assert approval.authoritative_span.role == REVIEW_TEXT_ROLE
    assert approval.authoritative_span.text_representation_id == graph.representation.id
    assert (approval.authoritative_span.start_offset,
            approval.authoritative_span.end_offset) == (3, 5)
    assert approval.approved_source_span.role == APPROVED_MEDIA_ROLE
    assert approval.approved_source_span.asset_id == audio.id
    assert (approval.approved_source_span.start_ms,
            approval.approved_source_span.end_ms) == (1200, 1800)
    assert project.text_segments.list_segments(graph.layer.id) == transcript_before
    assert (
        project.text_segments.list_segments(mfa.layers[0].id),
        project.text_segments.list_segment_text_spans(mfa.segments[0].id),
        project.text_segments.list_segment_media_spans(mfa.segments[0].id),
    ) == mfa_before
    assert approval.processing_run.process_type == "review"
    assert approval.processing_run.tool_name == "LexBundler"
    assert approval.processing_run.parameters["manually_edited"] is True
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == files_before
    assert not any(
        run.process_type == "media_render"
        for run in project.corpus.list_processing_runs()
    )

    project.close_project()
    project.open_project(project_path)
    selection = project.alignment_review.load(source.id, None)
    assert selection.utterances[0].approval is None
    assert (selection.utterances[1].approval.start_ms,
            selection.utterances[1].approval.end_ms) == (1200, 1800)


def test_reapproval_is_append_only_and_latest_successful_is_current(tmp_path: Path) -> None:
    project, _path, source, graph, audio, _audio_path = _context(tmp_path, "一句")
    first = project.pedagogical_reviews.approve(
        _request(project, graph, audio, start=500, end=900)
    )
    second = project.pedagogical_reviews.approve(
        _request(project, graph, audio, start=450, end=950, edited=True)
    )

    history = project.alignment_review.list_review_history(
        source.id, None, graph.segments[0].id
    )
    assert [item.processing_run_id for item in history] == [
        second.processing_run.id, first.processing_run.id
    ]
    current = project.alignment_review.load(source.id, None).utterances[0].approval
    assert (current.start_ms, current.end_ms) == (450, 950)
    reviewed_layers = [
        layer for layer in project.text_segments.list_segment_layers(source.id)
        if layer.layer_kind == REVIEW_LAYER_KIND
    ]
    assert len(reviewed_layers) == 2


@pytest.mark.parametrize("start,end", [(-1, 10), (10, 10), (20, 10)])
def test_invalid_approval_bounds_are_rejected_without_run(
    tmp_path: Path, start: int, end: int
) -> None:
    project, _path, _source, graph, audio, _audio_path = _context(tmp_path, "一句")
    with pytest.raises(PedagogicalReviewError, match="non-empty nonnegative"):
        project.pedagogical_reviews.approve(
            _request(project, graph, audio, start=start, end=end)
        )
    assert not any(
        run.process_type == "review" for run in project.corpus.list_processing_runs()
    )


def test_approval_outside_review_context_is_rejected(tmp_path: Path) -> None:
    project, _path, _source, graph, audio, _audio_path = _context(tmp_path, "一句")
    request = replace(
        _request(project, graph, audio),
        approved_start_ms=0, approved_end_ms=4000,
        mfa_speech_start_ms=1000, mfa_speech_end_ms=2000,
    )
    with pytest.raises(PedagogicalReviewError, match="context window"):
        project.pedagogical_reviews.approve(request)


def test_approval_graph_failure_rolls_back_and_marks_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, project_path, _source, graph, audio, _audio_path = _context(tmp_path, "一句")
    original = sqlite_text_store._insert_segment

    def fail(*args, **kwargs):
        if kwargs.get("kind") == REVIEW_SEGMENT_KIND:
            raise RuntimeError("review graph failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_text_store, "_insert_segment", fail)
    with pytest.raises(RuntimeError, match="review graph failure"):
        project.pedagogical_reviews.approve(_request(project, graph, audio))
    with sqlite3.connect(project_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM segment_layer WHERE layer_kind = ?",
            (REVIEW_LAYER_KIND,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM processing_run WHERE process_type = 'review'"
        ).fetchone()[0] == "failed"
