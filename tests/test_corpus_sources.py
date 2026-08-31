from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import CorpusIntegrityError, InvalidCorpusDataError
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project_service = ProjectService(SQLiteProjectStoreFactory())
    project_service.create_project(tmp_path / "corpus.lexbundler", name="Corpus")
    return project_service


def test_create_get_and_list_language_neutral_sources(
    service: ProjectService,
) -> None:
    manual = service.corpus.create_source("Manual Source")
    imported = service.corpus.create_source(
        "Course Material",
        source_kind="course",
        language_tag="cmn-Hans-CN",
        external_id="provider-42",
        metadata={"edition": 3, "labels": ["student", "audio"]},
    )

    assert manual.source_kind is None
    assert manual.language_tag is None
    assert manual.external_id is None
    assert manual.created_by_run_id is None
    assert service.corpus.get_source(imported.id) == imported
    assert service.corpus.list_sources() == [manual, imported]
    assert imported.metadata == {"edition": 3, "labels": ["student", "audio"]}


def test_arbitrary_source_unit_hierarchy_and_optional_fields(
    service: ProjectService,
) -> None:
    source = service.corpus.create_source("Source")
    book = service.corpus.create_source_unit(
        source.id, kind="book", label="Workbook", confidence=0.0
    )
    lesson = service.corpus.create_source_unit(
        source.id,
        kind="lesson",
        label="Lesson 1",
        parent_id=book.id,
        sequence=1,
        external_id="lesson-one",
    )
    section = service.corpus.create_source_unit(
        source.id,
        kind="section",
        label="Listening",
        parent_id=lesson.id,
        confidence=1.0,
    )

    assert service.corpus.get_source_unit(section.id) == section
    assert book.sequence is None
    assert book.external_id is None
    assert [unit.id for unit in service.corpus.list_source_units(source.id)] == [
        lesson.id,
        book.id,
        section.id,
    ]


def test_cross_source_parent_is_rejected(service: ProjectService) -> None:
    first = service.corpus.create_source("First")
    second = service.corpus.create_source("Second")
    parent = service.corpus.create_source_unit(first.id, kind="group", label="Parent")

    with pytest.raises(CorpusIntegrityError):
        service.corpus.create_source_unit(
            second.id, kind="group", label="Child", parent_id=parent.id
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_source_unit_confidence_outside_bounds_is_rejected(
    service: ProjectService, confidence: float
) -> None:
    source = service.corpus.create_source("Source")
    with pytest.raises(InvalidCorpusDataError):
        service.corpus.create_source_unit(
            source.id, kind="unit", label="Unit", confidence=confidence
        )

