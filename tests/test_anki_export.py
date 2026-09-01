"""Tests for the explicit Listening v1 Anki export vertical slice."""

import json
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import genanki
import pytest

from lexbundler.application.anki_export_service import (
    AnkiExportItem,
    anki_deck_id,
    lexbundler_listening_id,
    media_basename,
)
from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import AnkiExportError
from lexbundler.domain.text_segments import SegmentMediaSpan, SegmentTextSpan
from lexbundler.exporters.anki import (
    LISTENING_BACK,
    LISTENING_CARD_VERSION,
    LISTENING_CSS,
    LISTENING_FIELDS,
    LISTENING_FRONT,
    LISTENING_MODEL_ID,
    listening_note_guid,
)
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory


@dataclass(frozen=True, slots=True)
class ExportContext:
    project: ProjectService
    project_path: Path
    source_id: int
    unit_id: int
    text_spans: tuple[SegmentTextSpan, ...]
    media_spans: tuple[SegmentMediaSpan, ...]
    media_paths: tuple[Path, ...]


@pytest.fixture
def export_context(tmp_path: Path) -> ExportContext:
    project_path = tmp_path / "anki.lexbundler"
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(project_path, name="Synthetic")
    source = project.corpus.create_source("Synthetic & Source")
    parent = project.corpus.create_source_unit(
        source.id, kind="lesson", label="Lesson <One>"
    )
    unit = project.corpus.create_source_unit(
        source.id, kind="text", label="Text 1", parent_id=parent.id
    )
    content = "prefix 你好<&世界 suffix 第二句。"
    representation = project.text_segments.create_text_representation(
        source.id,
        representation_kind="reviewed",
        content=content,
        source_unit_id=unit.id,
        language_tag="zh-Hans",
    )
    layer = project.text_segments.create_segment_layer(
        source.id,
        name="Reviewed",
        layer_kind="utterance",
        source_unit_id=unit.id,
        language_tag="zh-Hans",
    )
    spans: list[SegmentTextSpan] = []
    media_spans: list[SegmentMediaSpan] = []
    paths: list[Path] = []
    for index, (start, end, payload) in enumerate(
        ((7, 12, b"ID3first synthetic audio"), (20, 24, b"ID3second synthetic audio"))
    ):
        segment = project.text_segments.create_segment(
            layer.id, kind="utterance", sequence=index
        )
        spans.append(
            project.text_segments.add_segment_text_span(
                segment.id, representation.id, start, end, role="content"
            )
        )
        media_path = tmp_path / "original arbitrary names" / f"clip {index}.mp3"
        media_path.parent.mkdir(exist_ok=True)
        media_path.write_bytes(payload)
        asset = project.corpus.register_local_asset(
            media_path, asset_kind="audio", mime_type="audio/mpeg"
        )
        media_spans.append(
            project.text_segments.add_segment_media_span(
                segment.id, asset.id, 0, 1000, role="rendered_clip"
            )
        )
        paths.append(media_path)
    return ExportContext(
        project,
        project_path,
        source.id,
        unit.id,
        tuple(spans),
        tuple(media_spans),
        tuple(paths),
    )


def _package_data(path: Path, extraction: Path) -> tuple[list[list[str]], dict, dict]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert "collection.anki2" in names
        assert "media" in names
        archive.extract("collection.anki2", extraction)
        media_map = json.loads(archive.read("media"))
        assert set(media_map) <= names
    with sqlite3.connect(extraction / "collection.anki2") as connection:
        note_rows = connection.execute("SELECT flds, tags, guid FROM notes ORDER BY id").fetchall()
        card_count = connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        models = json.loads(connection.execute("SELECT models FROM col").fetchone()[0])
    assert card_count == len(note_rows)
    return [row[0].split("\x1f") + [row[1], row[2]] for row in note_rows], media_map, models


def _run_rows(path: Path) -> list[tuple[str, str, str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT process_type, tool_name, parameters_json, status "
            "FROM processing_run ORDER BY id"
        ).fetchall()


def test_genanki_python_compatibility_smoke(tmp_path: Path) -> None:
    model = genanki.Model(
        1_700_000_001,
        "Smoke",
        fields=[{"name": "Audio"}, {"name": "Answer"}],
        templates=[{"name": "Card", "qfmt": "{{Audio}}", "afmt": "{{Answer}}"}],
    )
    note = genanki.Note(model=model, fields=["[sound:tiny.mp3]", "ok"])
    deck = genanki.Deck(1_700_000_002, "Smoke")
    deck.add_note(note)
    media = tmp_path / "tiny.mp3"
    media.write_bytes(b"ID3smoke")
    output = tmp_path / "smoke.apkg"
    genanki.Package(deck, media_files=[str(media)]).write_to_file(str(output))
    assert output.stat().st_size > 0


def test_one_note_export_content_media_and_provenance(
    export_context: ExportContext, tmp_path: Path
) -> None:
    output = tmp_path / "durable" / "listening.apkg"
    item = AnkiExportItem(
        export_context.text_spans[0].id,
        export_context.media_spans[0].id,
        ("synthetic", "lesson1"),
    )
    result = export_context.project.anki_exports.export_apkg(
        deck_name="Synthetic Listening", output_path=output, items=[item]
    )

    assert result.note_count == 1
    assert result.output_path == output.resolve()
    assert output.is_file() and output.stat().st_size > 0
    assert result.processing_run.status == "succeeded"
    assert result.processing_run.process_type == "export"
    assert result.processing_run.tool_name == "LexBundler"
    assert result.processing_run.parameters == {
        "export_format": "anki-apkg",
        "deck_name": "Synthetic Listening",
        "note_model": "listening-v1",
        "item_count": 1,
    }
    assert result.package_asset.created_by_run_id == result.processing_run.id
    locations = export_context.project.corpus.list_asset_locations(
        result.package_asset.id
    )
    assert [location.location for location in locations] == [str(output.resolve())]
    assert all("lexbundler-anki-" not in location.location for location in locations)

    rows, media_map, models = _package_data(output, tmp_path / "inspect-one")
    expected_asset = export_context.project.corpus.get_asset(
        export_context.media_spans[0].asset_id
    )
    basename = media_basename(expected_asset)
    assert len(rows) == 1
    fields = rows[0][:6]
    assert fields == [
        f"[sound:{basename}]",
        "你好&lt;&amp;世",
        "",
        "",
        "Synthetic &amp; Source — Lesson &lt;One&gt; — Text 1",
        lexbundler_listening_id(
            export_context.project.current_project.project_uuid,
            export_context.text_spans[0].segment_id,
        ),
    ]
    assert set(rows[0][6].split()) == {"lexbundler", "synthetic", "lesson1"}
    assert rows[0][7] == listening_note_guid(
        export_context.project.current_project.project_uuid,
        export_context.text_spans[0].segment_id,
    )
    assert list(media_map.values()) == [basename]
    assert str(export_context.media_paths[0]) not in fields[0]
    with zipfile.ZipFile(output) as archive:
        packaged_name = next(key for key, value in media_map.items() if value == basename)
        assert archive.read(packaged_name) == export_context.media_paths[0].read_bytes()
    model = models[str(LISTENING_MODEL_ID)]
    assert [field["name"] for field in model["flds"]] == list(LISTENING_FIELDS)
    assert model["name"] == "LexBundler Listening v1"
    assert model["tmpls"][0]["qfmt"] == LISTENING_FRONT
    assert model["tmpls"][0]["afmt"] == LISTENING_BACK


def test_multi_note_export_uses_distinct_hashed_media(
    export_context: ExportContext, tmp_path: Path
) -> None:
    output = tmp_path / "multi.apkg"
    result = export_context.project.anki_exports.export_apkg(
        deck_name="Multi",
        output_path=output,
        items=[
            AnkiExportItem(export_context.text_spans[index].id, export_context.media_spans[index].id)
            for index in range(2)
        ],
    )
    rows, media_map, _ = _package_data(output, tmp_path / "inspect-multi")
    assert result.note_count == 2
    basenames = {
        media_basename(export_context.project.corpus.get_asset(span.asset_id))
        for span in export_context.media_spans
    }
    assert len(basenames) == 2
    assert set(media_map.values()) == basenames
    assert {row[0] for row in rows} == {f"[sound:{name}]" for name in basenames}


def test_repeated_asset_is_packaged_once(
    export_context: ExportContext, tmp_path: Path
) -> None:
    second_span = export_context.project.text_segments.add_segment_media_span(
        export_context.text_spans[1].segment_id,
        export_context.media_spans[0].asset_id,
        0,
        1000,
        role="rendered_clip",
    )
    output = tmp_path / "shared.apkg"
    export_context.project.anki_exports.export_apkg(
        deck_name="Shared",
        output_path=output,
        items=[
            AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[0].id),
            AnkiExportItem(export_context.text_spans[1].id, second_span.id),
        ],
    )
    rows, media_map, _ = _package_data(output, tmp_path / "inspect-shared")
    assert len(rows) == 2
    assert len(media_map) == 1
    assert rows[0][0] == rows[1][0]


def test_mismatched_or_nonrendered_spans_fail_before_run(
    export_context: ExportContext, tmp_path: Path
) -> None:
    with pytest.raises(AnkiExportError, match="same Segment"):
        export_context.project.anki_exports.export_apkg(
            deck_name="Mismatch",
            output_path=tmp_path / "mismatch.apkg",
            items=[AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[1].id)],
        )
    source_role = export_context.project.text_segments.add_segment_media_span(
        export_context.text_spans[0].segment_id,
        export_context.media_spans[0].asset_id,
        0,
        10,
        role="source",
    )
    with pytest.raises(AnkiExportError, match="rendered_clip"):
        export_context.project.anki_exports.export_apkg(
            deck_name="Wrong role",
            output_path=tmp_path / "wrong-role.apkg",
            items=[AnkiExportItem(export_context.text_spans[0].id, source_role.id)],
        )
    assert _run_rows(export_context.project_path) == []


def test_stale_local_media_fails_before_run(
    export_context: ExportContext, tmp_path: Path
) -> None:
    export_context.media_paths[0].unlink()
    with pytest.raises(AnkiExportError, match="no currently usable local file"):
        export_context.project.anki_exports.export_apkg(
            deck_name="Stale",
            output_path=tmp_path / "stale.apkg",
            items=[AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[0].id)],
        )
    assert _run_rows(export_context.project_path) == []


def test_templates_css_and_empty_fields_are_pedagogically_scoped() -> None:
    assert LISTENING_FRONT == "{{Audio}}"
    for field in ("ChineseSC", "Pinyin", "English", "Source", "LexBundlerID"):
        assert "{{" + field + "}}" not in LISTENING_FRONT
    assert LISTENING_BACK.startswith("{{FrontSide}}")
    assert "<hr id=answer>" in LISTENING_BACK
    assert LISTENING_BACK.count("{{Audio}}") == 0
    for field in ("ChineseSC", "Pinyin", "English", "Source"):
        assert "{{" + field + "}}" in LISTENING_BACK
    assert "Pinyin:" not in LISTENING_BACK and "English:" not in LISTENING_BACK
    assert "[Pinyin]" not in LISTENING_BACK and "TODO" not in LISTENING_BACK
    assert '"Kaiti SC", "STKaiti", "KaiTi", serif' in LISTENING_CSS
    assert "font-size: 32px" in LISTENING_CSS
    assert "font-size: 12px" in LISTENING_CSS
    assert ":empty" in LISTENING_CSS
    assert "@font-face" not in LISTENING_CSS


def test_guid_deck_and_model_identity_are_stable() -> None:
    project_one = UUID("00000000-0000-0000-0000-000000000001")
    project_two = UUID("00000000-0000-0000-0000-000000000002")
    guid = listening_note_guid(project_one, 7)
    assert guid == listening_note_guid(project_one, 7)
    assert guid != listening_note_guid(project_one, 8)
    assert guid != genanki.guid_for(project_one, 7, "production-v1")
    assert guid == genanki.guid_for(project_one, 7, LISTENING_CARD_VERSION)
    assert anki_deck_id(project_one, "Deck") == anki_deck_id(project_one, "Deck")
    assert anki_deck_id(project_one, "Deck") != anki_deck_id(project_one, "Other")
    assert anki_deck_id(project_one, "Deck") != anki_deck_id(project_two, "Deck")
    assert 1 << 30 <= anki_deck_id(project_one, "Deck") < 1 << 31
    assert LISTENING_MODEL_ID == 1_778_120_801


def test_existing_output_and_invalid_tags_fail_before_run(
    export_context: ExportContext, tmp_path: Path
) -> None:
    output = tmp_path / "exists.apkg"
    output.write_bytes(b"keep exactly")
    with pytest.raises(AnkiExportError, match="already exists"):
        export_context.project.anki_exports.export_apkg(
            deck_name="Collision",
            output_path=output,
            items=[AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[0].id)],
        )
    assert output.read_bytes() == b"keep exactly"
    with pytest.raises(AnkiExportError, match="no whitespace"):
        export_context.project.anki_exports.export_apkg(
            deck_name="Tags",
            output_path=tmp_path / "tags.apkg",
            items=[AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[0].id, ("bad tag",))],
        )
    assert _run_rows(export_context.project_path) == []


@pytest.mark.parametrize(("exception", "status"), [(RuntimeError("fail"), "failed"), (KeyboardInterrupt(), "cancelled")])
def test_post_start_failures_finish_run(
    export_context: ExportContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
    status: str,
) -> None:
    def fail_writer(**_kwargs):
        raise exception

    monkeypatch.setattr(
        "lexbundler.application.anki_export_service.write_listening_package",
        fail_writer,
    )
    with pytest.raises(type(exception)) as caught:
        export_context.project.anki_exports.export_apkg(
            deck_name="Failure",
            output_path=tmp_path / f"{status}.apkg",
            items=[AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[0].id)],
        )
    assert caught.value is exception
    assert _run_rows(export_context.project_path)[0][3] == status


def test_deduplicated_package_keeps_original_creator(
    export_context: ExportContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing_path = tmp_path / "existing-package-bytes.apkg"
    existing_path.write_bytes(b"synthetic package bytes")
    original_run = export_context.project.corpus.start_processing_run("synthetic")
    existing_asset = export_context.project.corpus.register_local_asset(
        existing_path,
        asset_kind="package",
        mime_type="application/vnd.anki",
        created_by_run_id=original_run.id,
    )
    export_context.project.corpus.finish_processing_run(original_run.id, status="succeeded")

    def fixed_writer(*, output_path: Path, **_kwargs) -> None:
        output_path.write_bytes(b"synthetic package bytes")

    monkeypatch.setattr(
        "lexbundler.application.anki_export_service.write_listening_package",
        fixed_writer,
    )
    result = export_context.project.anki_exports.export_apkg(
        deck_name="Dedup",
        output_path=tmp_path / "dedup.apkg",
        items=[AnkiExportItem(export_context.text_spans[0].id, export_context.media_spans[0].id)],
    )
    assert result.package_asset.id == existing_asset.id
    assert result.package_asset.created_by_run_id == original_run.id
    assert {location.location for location in export_context.project.corpus.list_asset_locations(existing_asset.id)} == {
        str(existing_path.resolve()),
        str((tmp_path / "dedup.apkg").resolve()),
    }
