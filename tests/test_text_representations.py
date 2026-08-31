import unicodedata
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import CorpusIntegrityError
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project_service = ProjectService(SQLiteProjectStoreFactory())
    project_service.create_project(tmp_path / "text.lexbundler", name="Text")
    return project_service


def test_text_representation_round_trips_exact_content_and_metadata(
    service: ProjectService,
) -> None:
    source = service.corpus.create_source("Source")
    content = "e\u0301\r\n你好\n"
    representation = service.text_segments.create_text_representation(
        source.id,
        representation_kind="custom_free_form_kind",
        content=content,
        metadata={"engine": "synthetic", "page": 2},
    )

    assert representation.language_tag is None
    assert representation.source_unit_id is None
    assert representation.source_asset_id is None
    assert representation.derived_from_id is None
    assert representation.created_by_run_id is None
    assert representation.content == content
    assert representation.content != unicodedata.normalize("NFC", content)
    assert service.text_segments.get_text_representation(representation.id) == representation
    assert service.text_segments.list_text_representations(source.id) == [representation]


def test_representation_context_asset_lineage_and_run_provenance(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Source")
    unit = service.corpus.create_source_unit(source.id, kind="part", label="Part")
    evidence = tmp_path / "source.txt"
    evidence.write_bytes(b"source bytes")
    asset = service.corpus.register_local_asset(evidence)
    run = service.corpus.start_processing_run("text_extraction")
    raw = service.text_segments.create_text_representation(
        source.id,
        source_unit_id=unit.id,
        representation_kind="extracted",
        language_tag="zh-Hant",
        content="原始文字",
        source_asset_id=asset.id,
        created_by_run_id=run.id,
    )
    reviewed = service.text_segments.create_text_representation(
        source.id,
        representation_kind="reviewed-by-person",
        content="原始文字",
        derived_from_id=raw.id,
    )

    assert raw.source_asset_id == asset.id
    assert raw.created_by_run_id == run.id
    assert reviewed.derived_from_id == raw.id
    assert reviewed.id != raw.id
    assert reviewed.content == raw.content


def test_identical_text_is_not_deduplicated(service: ProjectService) -> None:
    first_source = service.corpus.create_source("First")
    second_source = service.corpus.create_source("Second")
    first = service.text_segments.create_text_representation(
        first_source.id, representation_kind="manual", content="没关系"
    )
    repeated = service.text_segments.create_text_representation(
        first_source.id, representation_kind="independent_run", content="没关系"
    )
    other_source = service.text_segments.create_text_representation(
        second_source.id, representation_kind="manual", content="没关系"
    )

    assert len({first.id, repeated.id, other_source.id}) == 3


def test_cross_source_unit_and_lineage_are_rejected(service: ProjectService) -> None:
    first = service.corpus.create_source("First")
    second = service.corpus.create_source("Second")
    other_unit = service.corpus.create_source_unit(second.id, kind="part", label="Part")
    other_text = service.text_segments.create_text_representation(
        second.id, representation_kind="raw", content="other"
    )

    with pytest.raises(CorpusIntegrityError):
        service.text_segments.create_text_representation(
            first.id,
            source_unit_id=other_unit.id,
            representation_kind="manual",
            content="text",
        )
    with pytest.raises(CorpusIntegrityError):
        service.text_segments.create_text_representation(
            first.id,
            representation_kind="derived",
            content="text",
            derived_from_id=other_text.id,
        )

