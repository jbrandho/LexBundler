from datetime import UTC
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import ProjectAlreadyOpenError
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


@pytest.fixture
def service() -> ProjectService:
    return ProjectService(SQLiteProjectStoreFactory())


def test_project_lifecycle_and_metadata_survive_reopen(
    service: ProjectService, tmp_path: Path
) -> None:
    path = tmp_path / "Mandarin.lexbundler"
    assert service.current_project is None

    created = service.create_project(
        path,
        name="Mandarin Corpus",
        primary_language_tag="cmn-Hans-CN",
        primary_language_name="Mandarin Chinese",
    )
    assert service.current_project == created
    assert created.created_at.tzinfo is UTC
    assert created.updated_at.tzinfo is UTC

    service.close_project()
    service.close_project()
    assert service.current_project is None

    reopened = service.open_project(path)
    assert reopened.project_uuid == created.project_uuid
    assert reopened.name == created.name
    assert reopened.primary_language_tag == created.primary_language_tag
    assert reopened.primary_language_name == created.primary_language_name
    assert reopened.created_at == created.created_at
    assert service.current_project == reopened


def test_creation_appends_project_extension(
    service: ProjectService, tmp_path: Path
) -> None:
    service.create_project(tmp_path / "Spanish", name="Spanish Corpus")
    assert (tmp_path / "Spanish.lexbundler").is_file()


def test_explicit_close_required_before_create_or_open(
    service: ProjectService, tmp_path: Path
) -> None:
    first = tmp_path / "first.lexbundler"
    second = tmp_path / "second.lexbundler"
    service.create_project(first, name="First")

    with pytest.raises(ProjectAlreadyOpenError):
        service.create_project(second, name="Second")
    with pytest.raises(ProjectAlreadyOpenError):
        service.open_project(first)

    assert service.current_project is not None
    assert service.current_project.name == "First"
    assert not second.exists()


def test_service_does_not_expose_sqlite_connection(
    service: ProjectService, tmp_path: Path
) -> None:
    service.create_project(tmp_path / "project.lexbundler", name="Project")
    current = service.current_project

    assert current is not None
    assert not hasattr(current, "execute")
    assert not hasattr(service, "connection")

