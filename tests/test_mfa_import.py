import json
import sqlite3
from pathlib import Path

import pytest

from lexbundler.application.mfa_import_service import match_mfa_words
from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import MfaTextMismatchError
from lexbundler.importers.mfa_hf_json import MfaInterval
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory
import lexbundler.persistence.sqlite.text_segment_store as sqlite_text_store


def _project(tmp_path: Path) -> ProjectService:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "mfa.lexbundler", name="MFA")
    return project


def _json(path: Path) -> bytes:
    document = {"start": 0, "end": 1.0, "tiers": {
        "words": {"type": "IntervalTier", "entries": [
            [0, .1, "<eps>"], [.1, .3, "你好"], [.3, .6, "要不"],
            [.6, .8, "要"], [.8, 1, "<eps>"],
        ]},
        "phones": {"type": "IntervalTier", "entries": [
            [0, .1, "sil"], [.1, .2, "n"], [.2, .3, "i"],
            [.3, .8, "jɑʊ"], [.8, 1, "sil"],
        ]},
    }}
    payload = json.dumps(document, ensure_ascii=False).encode()
    path.write_bytes(payload)
    return payload


def test_matcher_skips_only_intervening_punctuation_and_space() -> None:
    words = (
        MfaInterval(0, 1, "要不", False),
        MfaInterval(1, 2, "要", False),
    )
    assert match_mfa_words("  要不要。", words) == ((2, 4), (4, 5))
    with pytest.raises(MfaTextMismatchError):
        match_mfa_words("要不要错", words)


def test_import_preserves_native_asset_and_normalizes_word_phone_silence(
    tmp_path: Path,
) -> None:
    service = _project(tmp_path)
    source = service.corpus.create_source("S", language_tag="zh")
    transcript = tmp_path / "t.txt"
    transcript.write_text("你好，要不要！", encoding="utf-8", newline="")
    authoritative = service.transcript_imports.import_utf8(
        transcript, source_id=source.id, language_tag="zh"
    ).graph.representation
    whisper_layer = service.text_segments.create_segment_layer(
        source.id, name="whisper.cpp raw ASR", layer_kind="asr"
    )
    media_path = tmp_path / "audio.wav"
    media_path.write_bytes(b"wave")
    media = service.corpus.register_local_asset(media_path, asset_kind="audio")
    artifact = tmp_path / "alignment.json"
    payload = _json(artifact)

    result = service.mfa_imports.import_json(
        artifact, media_asset=media, authoritative_text=authoritative,
        source_id=source.id,
    )
    assert artifact.read_bytes() == payload
    assert [layer.name for layer in result.graph.layers] == [
        "MFA word alignment", "MFA phone alignment"
    ]
    words = result.graph.segments[:5]
    phones = result.graph.segments[5:]
    assert [segment.label for segment in words] == ["<eps>", "你好", "要不", "要", "<eps>"]
    assert [segment.label for segment in phones] == ["sil", "n", "i", "jɑʊ", "sil"]
    assert [service.text_segments.resolve_text_span(span.id)
            for span in result.graph.text_spans] == ["你好", "要不", "要"]
    assert all(span.asset_id == media.id for span in result.graph.media_spans)
    assert result.processing_run.parameters["operation"] == "normalize_existing_artifact"
    assert result.processing_run.tool_name == "LexBundler"
    layers = service.text_segments.list_segment_layers(source.id)
    assert len(layers) == 4
    assert any(layer.id == whisper_layer.id for layer in layers)

    repeated = service.mfa_imports.import_json(
        artifact, media_asset=media, authoritative_text=authoritative,
        source_id=source.id,
    )
    assert repeated.json_asset.id == result.json_asset.id
    assert repeated.graph.layers[0].id != result.graph.layers[0].id


def test_alignment_graph_rolls_back_without_touching_existing_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _project(tmp_path)
    source = service.corpus.create_source("S")
    transcript = tmp_path / "t.txt"
    transcript.write_text("你好，要不要！", encoding="utf-8")
    authoritative = service.transcript_imports.import_utf8(
        transcript, source_id=source.id
    ).graph.representation
    media_path = tmp_path / "a.wav"
    media_path.write_bytes(b"audio")
    media = service.corpus.register_local_asset(media_path)
    artifact = tmp_path / "a.json"
    _json(artifact)
    original = sqlite_text_store._insert_segment
    calls = 0

    def fail(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("synthetic alignment failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_text_store, "_insert_segment", fail)
    with pytest.raises(RuntimeError, match="alignment failure"):
        service.mfa_imports.import_json(
            artifact, media_asset=media, authoritative_text=authoritative,
            source_id=source.id,
        )
    with sqlite3.connect(tmp_path / "mfa.lexbundler") as connection:
        assert connection.execute("SELECT COUNT(*) FROM segment_layer").fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM processing_run ORDER BY id DESC"
        ).fetchone()[0] == "failed"
