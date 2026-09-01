"""Integration tests for manual whisper.cpp JSON normalization."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import WhisperCppFormatError
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory
import lexbundler.persistence.sqlite.text_segment_store as sqlite_text_store


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "import.lexbundler", name="Import")
    return project


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    media_bytes = b"synthetic media bytes\x00\x01"
    document = {
        "params": {
            "model": "/outside/ggml-large-v3.bin",
            "language": "zh",
            "translate": False,
        },
        "model": {"type": "large"},
        "result": {"language": "zh"},
        "transcription": [
            {
                "text": "你好",
                "offsets": {"from": 920, "to": 5060},
                "tokens": [{"text": "not authoritative"}],
            },
            {"text": " world", "offsets": {"from": 5060, "to": 6100}},
            {"text": "", "offsets": {"from": 5900, "to": 6200}},
            {"text": "再见", "offsets": {"from": 6200, "to": 7000}},
        ],
    }
    json_bytes = json.dumps(document, ensure_ascii=False).encode()
    media_path = tmp_path / "sample.media"
    json_path = tmp_path / "sample.json"
    media_path.write_bytes(media_bytes)
    json_path.write_bytes(json_bytes)
    return json_path, media_path, json_bytes, media_bytes


def test_import_maps_exact_text_media_assets_and_provenance(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Authored source")
    unit = service.corpus.create_source_unit(
        source.id, kind="part", label="Part A"
    )
    json_path, media_path, json_bytes, media_bytes = _write_inputs(tmp_path)

    result = service.whisper_imports.import_json(
        json_path,
        media_path,
        source_id=source.id,
        source_unit_id=unit.id,
    )

    assert json_path.read_bytes() == json_bytes
    assert media_path.read_bytes() == media_bytes
    assert result.json_asset.sha256 == hashlib.sha256(json_bytes).hexdigest()
    assert result.media_asset.sha256 == hashlib.sha256(media_bytes).hexdigest()
    assert result.graph.representation.content == "你好 world再见"
    assert result.graph.representation.source_asset_id == result.json_asset.id
    assert result.graph.representation.created_by_run_id == result.processing_run.id
    assert result.graph.layer.name == "whisper.cpp raw ASR"
    assert result.graph.layer.layer_kind == "asr"
    assert result.graph.layer.language_tag == "zh"
    assert result.graph.layer.source_unit_id == unit.id
    assert result.graph.layer.created_by_run_id == result.processing_run.id

    assert [segment.sequence for segment in result.graph.segments] == [0, 1, 2, 3]
    assert [segment.external_id for segment in result.graph.segments] == [
        "0",
        "1",
        "2",
        "3",
    ]
    assert {segment.kind for segment in result.graph.segments} == {"asr_segment"}
    assert all(
        segment.created_by_run_id == result.processing_run.id
        for segment in result.graph.segments
    )
    assert [
        (span.start_ms, span.end_ms) for span in result.graph.media_spans
    ] == [(920, 5060), (5060, 6100), (5900, 6200), (6200, 7000)]
    assert all(span.asset_id == result.media_asset.id for span in result.graph.media_spans)
    assert all(
        span.created_by_run_id == result.processing_run.id
        for span in (*result.graph.text_spans, *result.graph.media_spans)
    )
    assert len(result.graph.text_spans) == 3
    assert [
        service.text_segments.resolve_text_span(span.id)
        for span in result.graph.text_spans
    ] == ["你好", " world", "再见"]
    empty_segment = result.graph.segments[2]
    assert service.text_segments.list_segment_text_spans(empty_segment.id) == []
    assert len(service.text_segments.list_segment_media_spans(empty_segment.id)) == 1

    bindings = service.corpus.list_asset_bindings(source.id)
    assert {(binding.asset_id, binding.role) for binding in bindings} == {
        (result.json_asset.id, "asr_output"),
        (result.media_asset.id, "source_media"),
    }
    assert all(binding.source_unit_id == unit.id for binding in bindings)
    assert all(binding.assignment_method == "manual_import" for binding in bindings)
    assert all(
        binding.processing_run_id == result.processing_run.id for binding in bindings
    )
    assert result.processing_run.process_type == "import"
    assert result.processing_run.tool_name == "LexBundler"
    assert result.processing_run.tool_version is None
    assert result.processing_run.status == "succeeded"
    assert result.processing_run.parameters["producer"] == "whisper.cpp"
    assert "tool_version" not in result.processing_run.parameters
    assert service.text_segments.list_speakers(source.id) == []


def test_complete_artifact_is_validated_before_any_write(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Parse-first source")
    document = {
        "transcription": [
            {"text": "valid first", "offsets": {"from": 0, "to": 10}},
            {"text": "malformed late", "offsets": {"from": 10, "to": True}},
        ]
    }
    json_path = tmp_path / "invalid.json"
    media_path = tmp_path / "unregistered.media"
    json_path.write_text(json.dumps(document))
    media_path.write_bytes(b"unregistered")

    with pytest.raises(WhisperCppFormatError, match=r"transcription\[1\]"):
        service.whisper_imports.import_json(
            json_path, media_path, source_id=source.id
        )

    with sqlite3.connect(tmp_path / "import.lexbundler") as connection:
        assert connection.execute("SELECT COUNT(*) FROM asset").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM processing_run").fetchone()[0]
            == 0
        )


def test_every_entry_is_retained_without_semantic_filtering(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Mixed source")
    document = {
        "transcription": [
            {"text": "Unit heading", "offsets": {"from": 0, "to": 10}},
            {"text": "English vocabulary", "offsets": {"from": 10, "to": 20}},
            {"text": "对话", "offsets": {"from": 20, "to": 30}},
        ]
    }
    json_path = tmp_path / "mixed.json"
    media_path = tmp_path / "mixed.bin"
    json_path.write_text(json.dumps(document, ensure_ascii=False))
    media_path.write_bytes(b"media")

    result = service.whisper_imports.import_json(
        json_path, media_path, source_id=source.id
    )

    assert len(result.graph.segments) == 3
    assert result.graph.representation.content == "Unit headingEnglish vocabulary对话"


def test_analytical_graph_rolls_back_and_run_fails(
    service: ProjectService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = service.corpus.create_source("Atomic source")
    json_path, media_path, _, _ = _write_inputs(tmp_path)
    original = sqlite_text_store._insert_flat_segment
    calls = 0

    def fail_during_graph(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic mid-graph failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_text_store, "_insert_flat_segment", fail_during_graph)

    with pytest.raises(RuntimeError, match="mid-graph"):
        service.whisper_imports.import_json(
            json_path, media_path, source_id=source.id
        )

    project_path = tmp_path / "import.lexbundler"
    with sqlite3.connect(project_path) as connection:
        for table in (
            "text_representation",
            "segment_layer",
            "segment",
            "segment_text_span",
            "segment_media_span",
        ):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        run = connection.execute(
            "SELECT process_type, tool_name, status FROM processing_run"
        ).fetchone()
    assert run == ("import", "LexBundler", "failed")
