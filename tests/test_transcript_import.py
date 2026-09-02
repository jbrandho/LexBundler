import hashlib
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "transcript.lexbundler", name="Transcript")
    return project


@pytest.mark.parametrize(
    "content",
    ["甲。\n\n乙？\n", "甲。\r\n\r\n乙？\r\n", "甲。\n\n乙？"],
)
def test_exact_content_and_nonempty_line_spans(
    service: ProjectService, tmp_path: Path, content: str
) -> None:
    source = service.corpus.create_source("Source", language_tag="zh")
    unit = service.corpus.create_source_unit(source.id, kind="dialogue", label="D")
    path = tmp_path / "transcript.txt"
    path.write_bytes(content.encode("utf-8"))
    result = service.transcript_imports.import_utf8(
        path, source_id=source.id, source_unit_id=unit.id, language_tag="zh"
    )
    assert result.graph.representation.content == content
    assert result.graph.representation.representation_kind == "authoritative_source"
    assert result.graph.representation.source_asset_id == result.transcript_asset.id
    assert [service.text_segments.resolve_text_span(span.id)
            for span in result.graph.text_spans] == ["甲。", "乙？"]
    assert result.processing_run.process_type == "import"
    assert result.processing_run.tool_name == "LexBundler"
    assert result.graph.layer.layer_kind == "transcript_line"


def test_five_turns_preserve_unicode_punctuation_and_asset_deduplication(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Dialogue")
    content = "周末你有什么打算？\n我早就想好了，请你吃饭。\n请我？\n是啊。\n我还没想好！"
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_bytes(content.encode())
    second.write_bytes(content.encode())
    a = service.transcript_imports.import_utf8(first, source_id=source.id)
    b = service.transcript_imports.import_utf8(second, source_id=source.id)
    assert a.transcript_asset.id == b.transcript_asset.id
    assert a.transcript_asset.sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert a.graph.representation.id != b.graph.representation.id
    assert len(a.graph.segments) == 5
    assert len(service.corpus.list_asset_locations(a.transcript_asset.id)) == 2

