import hashlib
from datetime import UTC
from pathlib import Path
from typing import BinaryIO

import pytest

from lexbundler.application.corpus_service import HASH_CHUNK_SIZE, _hash_local_file
from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import (
    AssetFileError,
    CorpusIntegrityError,
    InvalidCorpusDataError,
)
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project_service = ProjectService(SQLiteProjectStoreFactory())
    project_service.create_project(tmp_path / "corpus.lexbundler", name="Corpus")
    return project_service


def test_local_asset_hash_size_deduplication_and_unchanged_bytes(
    service: ProjectService, tmp_path: Path
) -> None:
    contents = b"small synthetic audio bytes\x00\x01"
    first_path = tmp_path / "first.mp3"
    second_path = tmp_path / "copy.mp3"
    first_path.write_bytes(contents)
    second_path.write_bytes(contents)

    first = service.corpus.register_local_asset(first_path)
    duplicate_path = service.corpus.register_local_asset(second_path)
    duplicate_registration = service.corpus.register_local_asset(first_path)

    assert first.sha256 == hashlib.sha256(contents).hexdigest()
    assert first.byte_size == len(contents)
    assert first.asset_kind == "audio"
    assert first.id == duplicate_path.id == duplicate_registration.id
    assert service.corpus.get_asset(first.id) == first
    assert service.corpus.find_asset_by_sha256(first.sha256) == first
    assert first_path.read_bytes() == contents
    assert second_path.read_bytes() == contents
    assert {location.location for location in service.corpus.list_asset_locations(first.id)} == {
        str(first_path.resolve()),
        str(second_path.resolve()),
    }


def test_hashing_reads_incremental_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "large.bin"
    contents = b"a" * (HASH_CHUNK_SIZE * 2 + 17)
    path.write_bytes(contents)
    original_open = Path.open
    read_sizes: list[int] = []

    class TrackingReader:
        def __init__(self, wrapped: BinaryIO) -> None:
            self.wrapped = wrapped

        def __enter__(self) -> "TrackingReader":
            return self

        def __exit__(self, *args: object) -> None:
            self.wrapped.close()

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self.wrapped.read(size)

    def tracking_open(file_path: Path, *args: object, **kwargs: object):
        wrapped = original_open(file_path, *args, **kwargs)
        if file_path == path:
            return TrackingReader(wrapped)
        return wrapped

    monkeypatch.setattr(Path, "open", tracking_open)
    digest, size = _hash_local_file(path)

    assert digest == hashlib.sha256(contents).hexdigest()
    assert size == len(contents)
    assert read_sizes and set(read_sizes) == {HASH_CHUNK_SIZE}


def test_different_bytes_and_historical_reuse_of_same_path(
    service: ProjectService, tmp_path: Path
) -> None:
    path = tmp_path / "changing.bin"
    path.write_bytes(b"first")
    first = service.corpus.register_local_asset(path)
    path.write_bytes(b"second")
    second = service.corpus.register_local_asset(path)

    assert first.id != second.id
    assert service.corpus.list_asset_locations(first.id)[0].location == str(
        path.resolve()
    )
    assert service.corpus.list_asset_locations(second.id)[0].location == str(
        path.resolve()
    )


def test_missing_and_directory_asset_paths_are_rejected(
    service: ProjectService, tmp_path: Path
) -> None:
    with pytest.raises(AssetFileError):
        service.corpus.register_local_asset(tmp_path / "missing")
    with pytest.raises(AssetFileError):
        service.corpus.register_local_asset(tmp_path)


def test_whole_source_and_unit_bindings_preserve_competing_assignments(
    service: ProjectService, tmp_path: Path
) -> None:
    source = service.corpus.create_source("Source")
    first_unit = service.corpus.create_source_unit(
        source.id, kind="section", label="One"
    )
    second_unit = service.corpus.create_source_unit(
        source.id, kind="section", label="Two"
    )
    first_file = tmp_path / "one.pdf"
    second_file = tmp_path / "two.pdf"
    first_file.write_bytes(b"document one")
    second_file.write_bytes(b"document two")
    first_asset = service.corpus.register_local_asset(first_file)
    second_asset = service.corpus.register_local_asset(second_file)
    run = service.corpus.start_processing_run("asset_assignment")

    whole = service.corpus.bind_asset_to_source(
        source.id,
        first_asset.id,
        role="source_document",
        assignment_method="manual-custom",
        confidence=0.0,
    )
    first_assignment = service.corpus.bind_asset_to_source_unit(
        source.id,
        first_unit.id,
        first_asset.id,
        role="anything-free-form",
        assignment_method="importer-v7",
        confidence=1.0,
        processing_run_id=run.id,
        metadata={"evidence": "synthetic"},
    )
    competing = service.corpus.bind_asset_to_source_unit(
        source.id, first_unit.id, first_asset.id, assignment_method="second-run"
    )
    other_unit = service.corpus.bind_asset_to_source_unit(
        source.id, second_unit.id, first_asset.id
    )
    other_asset = service.corpus.bind_asset_to_source_unit(
        source.id, first_unit.id, second_asset.id
    )

    assert whole.source_unit_id is None
    assert first_assignment.processing_run_id == run.id
    assert first_assignment.metadata == {"evidence": "synthetic"}
    assert len(
        {whole.id, first_assignment.id, competing.id, other_unit.id, other_asset.id}
    ) == 5
    assert len(service.corpus.list_asset_bindings(source.id)) == 5


def test_binding_source_unit_mismatch_and_confidence_are_rejected(
    service: ProjectService, tmp_path: Path
) -> None:
    first = service.corpus.create_source("First")
    second = service.corpus.create_source("Second")
    unit = service.corpus.create_source_unit(first.id, kind="unit", label="Unit")
    path = tmp_path / "asset.bin"
    path.write_bytes(b"asset")
    asset = service.corpus.register_local_asset(path)

    with pytest.raises(CorpusIntegrityError):
        service.corpus.bind_asset_to_source_unit(second.id, unit.id, asset.id)
    with pytest.raises(InvalidCorpusDataError):
        service.corpus.bind_asset_to_source(first.id, asset.id, confidence=1.1)


def test_processing_run_lifecycle_and_provenance(
    service: ProjectService,
) -> None:
    run = service.corpus.start_processing_run(
        "asset_import",
        parameters={"recursive": False, "extensions": [".wav", ".pdf"]},
    )

    assert run.run_uuid
    assert run.status == "running"
    assert run.started_at.tzinfo is UTC
    assert run.completed_at is None
    assert run.tool_name is None
    assert run.tool_version is None
    assert run.parameters == {"extensions": [".wav", ".pdf"], "recursive": False}

    failed = service.corpus.finish_processing_run(run.id, status="failed")
    assert failed.run_uuid == run.run_uuid
    assert failed.status == "failed"
    assert failed.completed_at is not None
    assert failed.completed_at.tzinfo is UTC
    assert service.corpus.get_processing_run(run.id) == failed


def test_processing_run_links_to_discovered_records(
    service: ProjectService, tmp_path: Path
) -> None:
    run = service.corpus.start_processing_run(
        "generic_import", tool_name="LexBundler", tool_version="0.1.0"
    )
    source = service.corpus.create_source("Imported", created_by_run_id=run.id)
    unit = service.corpus.create_source_unit(
        source.id,
        kind="arbitrary",
        label="Unit",
        created_by_run_id=run.id,
    )
    path = tmp_path / "evidence.bin"
    path.write_bytes(b"evidence")
    asset = service.corpus.register_local_asset(path, created_by_run_id=run.id)
    binding = service.corpus.bind_asset_to_source_unit(
        source.id,
        unit.id,
        asset.id,
        processing_run_id=run.id,
    )

    assert source.created_by_run_id == run.id
    assert unit.created_by_run_id == run.id
    assert asset.created_by_run_id == run.id
    assert service.corpus.list_asset_locations(asset.id)[0].created_by_run_id == run.id
    assert binding.processing_run_id == run.id
