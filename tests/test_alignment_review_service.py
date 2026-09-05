import json
from pathlib import Path

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


def _project(tmp_path: Path) -> ProjectService:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "review.lexbundler", name="Review")
    return project


def _aligned_source(project: ProjectService, tmp_path: Path, *, name="Dialogue", unit=None):
    source = project.corpus.create_source(name, language_tag="zh")
    transcript_path = tmp_path / f"{name}.txt"
    transcript_path.write_text("第一行\n要不要\n末行", encoding="utf-8", newline="")
    transcript = project.transcript_imports.import_utf8(
        transcript_path, source_id=source.id,
        source_unit_id=unit.id if unit else None, language_tag="zh",
    ).graph.representation
    audio_path = tmp_path / f"{name}.wav"
    audio_path.write_bytes(b"not-real-audio")
    audio = project.corpus.register_local_asset(audio_path, asset_kind="audio")
    artifact = tmp_path / f"{name}.json"
    artifact.write_text(json.dumps({
        "start": 0, "end": 2.2, "tiers": {
            "words": {"type": "IntervalTier", "entries": [
                [0, .1, "<eps>"], [.1, .5, "第一行"], [.5, .7, "<eps>"],
                [.7, 1.0, "要不"], [1.0, 1.2, "要"], [1.2, 1.5, "<eps>"],
                [1.5, 2.0, "末行"], [2.0, 2.2, "<eps>"],
            ]},
            "phones": {"type": "IntervalTier", "entries": [[0, 2.2, "sil"]]},
        },
    }, ensure_ascii=False), encoding="utf-8")
    result = project.mfa_imports.import_json(
        artifact, media_asset=audio, authoritative_text=transcript,
        source_id=source.id, source_unit_id=unit.id if unit else None,
    )
    return source, transcript, audio_path, result


def test_review_maps_exact_transcript_spans_words_bounds_and_silence(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source, _text, audio_path, _result = _aligned_source(project, tmp_path)
    project.text_segments.create_segment_layer(
        source.id, name="Whisper", layer_kind="asr"
    )

    selection = project.alignment_review.load(source.id, None)

    assert [item.sequence for item in selection.utterances] == [0, 1, 2]
    assert [item.text for item in selection.utterances] == ["第一行", "要不要", "末行"]
    middle = selection.utterances[1]
    assert (middle.text_start, middle.text_end) == (4, 7)
    assert [word.label for word in middle.words] == ["要不", "要"]
    assert (middle.speech_start_ms, middle.speech_end_ms) == (700, 1200)
    assert (middle.preceding_silence_start_ms, middle.following_silence_end_ms) == (500, 1500)
    assert middle.audio_path == audio_path.resolve()
    assert middle.playback_available


def test_review_excludes_items_without_alignment_or_local_audio(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = project.corpus.create_source("Text only")
    transcript_path = tmp_path / "only.txt"
    transcript_path.write_text("可见", encoding="utf-8")
    project.transcript_imports.import_utf8(transcript_path, source_id=source.id)

    assert project.alignment_review.load(source.id, None).utterances == ()

    aligned, _text, audio_path, _result = _aligned_source(project, tmp_path, name="Gone")
    audio_path.unlink()
    assert project.alignment_review.load(aligned.id, None).utterances == ()


def test_sources_units_are_isolated_and_latest_alignment_is_default(tmp_path: Path) -> None:
    project = _project(tmp_path)
    first = project.corpus.create_source("With unit")
    unit = project.corpus.create_source_unit(first.id, kind="lesson", label="Lesson 1")
    transcript_path = tmp_path / "unit.txt"
    transcript_path.write_text("单元", encoding="utf-8")
    project.transcript_imports.import_utf8(
        transcript_path, source_id=first.id, source_unit_id=unit.id
    )
    second, text, _audio_path, initial = _aligned_source(project, tmp_path, name="Second")
    artifact = tmp_path / "Second.json"
    audio = project.corpus.get_asset(initial.graph.media_spans[0].asset_id)
    repeated = project.mfa_imports.import_json(
        artifact, media_asset=audio, authoritative_text=text, source_id=second.id
    )

    assert [item.label for item in project.alignment_review.list_sources()] == ["With unit", "Second"]
    assert [item.label for item in project.alignment_review.list_units(first.id)] == ["Lesson 1"]
    assert project.alignment_review.load(first.id, None).utterances == ()
    assert project.alignment_review.load(first.id, unit.id).utterances == ()
    selection = project.alignment_review.load(second.id, None)
    assert len(selection.alignments) == 2
    assert selection.selected_alignment_layer_id == repeated.graph.layers[0].id
    old = project.alignment_review.load(
        second.id, None, alignment_layer_id=initial.graph.layers[0].id
    )
    assert old.selected_alignment_layer_id == initial.graph.layers[0].id
