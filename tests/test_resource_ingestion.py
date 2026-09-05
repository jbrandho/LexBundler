from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.application.resource_ingestion_service import (
    AssetAttachmentRequest, AssetAttachmentType, ResourceIngestionRequest,
    ResourceType, TextProvenance,
)
from lexbundler.domain.errors import ResourceIngestionError
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "ingestion.lexbundler", name="Ingestion")
    return project


def _files(tmp_path: Path) -> tuple[Path, Path]:
    audio = tmp_path / "speech.wav"
    text = tmp_path / "speech.txt"
    audio.write_bytes(b"synthetic audio evidence")
    text.write_bytes("你好\n再见\n".encode())
    return audio, text


@pytest.mark.parametrize(
    ("resource_type", "has_audio", "has_text"),
    (
        (ResourceType.AUDIO_TRANSCRIPT, True, True),
        (ResourceType.AUDIO_ONLY, True, False),
        (ResourceType.TEXT_ONLY, False, True),
    ),
)
def test_ingests_supported_resource_types(
    service: ProjectService, tmp_path: Path, resource_type: ResourceType,
    has_audio: bool, has_text: bool,
) -> None:
    audio, text = _files(tmp_path)
    result = service.resource_ingestion.ingest(ResourceIngestionRequest(
        resource_type=resource_type,
        resource_name=resource_type.label,
        new_source_name=f"Source {resource_type.value}",
        audio_path=audio if has_audio else None,
        text_path=text if has_text else None,
    ))

    unit = service.corpus.get_source_unit(result.resource.source_unit_id)
    bindings = service.corpus.list_asset_bindings(result.resource.source_id)
    assert unit.kind == "resource"
    assert unit.label == resource_type.label
    assert {binding.role for binding in bindings} == (
        ({"source_audio"} if has_audio else set())
        | ({"authoritative_transcript"} if has_text else set())
    )
    assert all(binding.processing_run_id == result.processing_run_id for binding in bindings)
    assert service.corpus.get_processing_run(result.processing_run_id).status == "succeeded"


@pytest.mark.parametrize(
    ("provenance", "representation_kind", "authority", "binding_role"),
    (
        (TextProvenance.AUTHORITATIVE, "authoritative_source", "source",
         "authoritative_transcript"),
        (TextProvenance.MACHINE_UNREVIEWED, "machine_transcript",
         "machine_unreviewed", "machine_transcript"),
    ),
)
def test_text_provenance_and_exact_content_are_preserved(
    service: ProjectService, tmp_path: Path, provenance: TextProvenance,
    representation_kind: str, authority: str, binding_role: str,
) -> None:
    text = tmp_path / f"{provenance.value}.txt"
    content = "甲。\r\n\r\n乙？\r\n"
    text.write_bytes(content.encode())
    result = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, provenance.label,
        new_source_name=f"Source {provenance.value}", text_path=text,
        text_provenance=provenance,
    ))

    representation = service.text_segments.get_text_representation(
        result.text_representation_id
    )
    binding = service.corpus.list_asset_bindings(result.resource.source_id)[0]
    assert representation.content == content
    assert representation.representation_kind == representation_kind
    assert representation.metadata["authority"] == authority
    assert binding.role == binding_role
    layers = service.text_segments.list_segment_layers(result.resource.source_id)
    assert layers[0].layer_kind == "transcript_line"


def test_reuses_existing_source_parent_and_references_original_files(
    service: ProjectService, tmp_path: Path,
) -> None:
    source = service.corpus.create_source("Course")
    parent = service.corpus.create_source_unit(
        source.id, kind="lesson", label="Lesson 1"
    )
    audio, text = _files(tmp_path)
    before_audio = audio.read_bytes()
    before_text = text.read_bytes()

    result = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_TRANSCRIPT, "Text 1",
        existing_source_id=source.id, existing_parent_unit_id=parent.id,
        audio_path=audio, text_path=text,
    ))

    unit = service.corpus.get_source_unit(result.resource.source_unit_id)
    locations = {
        location.location
        for asset_id in result.asset_ids
        for location in service.corpus.list_asset_locations(asset_id)
    }
    assert unit.parent_id == parent.id
    assert locations == {str(audio.resolve()), str(text.resolve())}
    assert audio.read_bytes() == before_audio
    assert text.read_bytes() == before_text


def test_creates_recursive_parent_path_and_explorer_resource(
    service: ProjectService, tmp_path: Path,
) -> None:
    _audio, text = _files(tmp_path)
    result = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "Asking Directions", new_source_name="ChinesePod",
        new_parent_labels=("Intermediate", "Travel"), text_path=text,
    ))

    units = service.corpus.list_source_units(result.resource.source_id)
    by_label = {unit.label: unit for unit in units}
    assert by_label["Travel"].parent_id == by_label["Intermediate"].id
    assert by_label["Asking Directions"].parent_id == by_label["Travel"].id
    tree = service.project_explorer.load_tree("Ingestion")
    assert tree.resource_count == 1
    assert tree.project.children[0].children[0].children[0].children[0].resource == result.resource


def test_asset_content_is_deduplicated_across_resources(
    service: ProjectService, tmp_path: Path,
) -> None:
    audio, _text = _files(tmp_path)
    copy = tmp_path / "same.wav"
    copy.write_bytes(audio.read_bytes())
    source = service.corpus.create_source("Audio")
    first = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_ONLY, "One", existing_source_id=source.id,
        audio_path=audio,
    ))
    second = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_ONLY, "Two", existing_source_id=source.id,
        audio_path=copy,
    ))
    assert first.asset_ids == second.asset_ids
    assert {location.location for location in service.corpus.list_asset_locations(
        first.asset_ids[0]
    )} == {str(audio.resolve()), str(copy.resolve())}


def test_resource_survives_close_and_reopen(
    service: ProjectService, tmp_path: Path,
) -> None:
    project_path = tmp_path / "ingestion.lexbundler"
    _audio, text = _files(tmp_path)
    result = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "Persistent", new_source_name="Source",
        text_path=text,
    ))
    service.close_project()
    service.open_project(project_path)
    assert service.corpus.get_source_unit(result.resource.source_unit_id).label == "Persistent"
    assert service.project_explorer.load_tree("Ingestion").resource_count == 1


def test_failure_rolls_back_entire_resource_graph(
    service: ProjectService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lexbundler.persistence.sqlite.resource_ingestion_store as store_module

    _audio, text = _files(tmp_path)
    original = store_module.dump_json

    def fail_during_transaction(value):
        if isinstance(value, dict) and "resource_type" in value:
            raise RuntimeError("synthetic persistence failure")
        return original(value)

    monkeypatch.setattr(store_module, "dump_json", fail_during_transaction)
    with pytest.raises(RuntimeError, match="synthetic persistence failure"):
        service.resource_ingestion.ingest(ResourceIngestionRequest(
            ResourceType.TEXT_ONLY, "Rollback", new_source_name="Transient",
            new_parent_labels=("Parent",), text_path=text,
        ))

    assert service.corpus.list_sources() == []
    assert service.corpus.list_processing_runs() == []
    assert service.project_explorer.load_tree("Ingestion").resource_count == 0


def test_missing_file_and_invalid_utf8_are_rejected_before_mutation(
    service: ProjectService, tmp_path: Path,
) -> None:
    source = service.corpus.create_source("Existing")
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(ResourceIngestionError, match="exact UTF-8"):
        service.resource_ingestion.ingest(ResourceIngestionRequest(
            ResourceType.TEXT_ONLY, "Bad text", existing_source_id=source.id,
            text_path=invalid,
        ))
    with pytest.raises(ResourceIngestionError, match="[Aa]udio file is required"):
        service.resource_ingestion.ingest(ResourceIngestionRequest(
            ResourceType.AUDIO_ONLY, "Missing", existing_source_id=source.id,
        ))
    assert service.corpus.list_source_units(source.id) == []
    assert service.corpus.list_processing_runs() == []


def test_json_is_rejected_as_generic_resource_text_before_mutation(
    service: ProjectService, tmp_path: Path,
) -> None:
    structured = tmp_path / "alignment.json"
    structured.write_text('{"tiers": {}}', encoding="utf-8")

    with pytest.raises(ResourceIngestionError, match="structured data"):
        service.resource_ingestion.ingest(ResourceIngestionRequest(
            ResourceType.TEXT_ONLY, "Invalid", new_source_name="Not created",
            text_path=structured,
        ))

    assert service.corpus.list_sources() == []
    assert service.corpus.list_processing_runs() == []


def test_json_is_rejected_as_generic_asset_text_before_mutation(
    service: ProjectService, tmp_path: Path,
) -> None:
    _audio, text = _files(tmp_path)
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "Existing", new_source_name="Source",
        text_path=text,
    ))
    structured = tmp_path / "whisper.json"
    structured.write_text('{"segments": []}', encoding="utf-8")
    bindings_before = service.corpus.list_asset_bindings(
        created.resource.source_id
    )
    runs_before = service.corpus.list_processing_runs()

    with pytest.raises(ResourceIngestionError, match="structured data"):
        service.resource_ingestion.add_asset_to_resource(
            AssetAttachmentRequest(
                created.resource, AssetAttachmentType.TEXT, structured
            )
        )

    assert service.corpus.list_asset_bindings(
        created.resource.source_id
    ) == bindings_before
    assert service.corpus.list_processing_runs() == runs_before


def test_duplicate_sibling_name_is_rejected_without_partial_import(
    service: ProjectService, tmp_path: Path,
) -> None:
    source = service.corpus.create_source("Existing")
    service.corpus.create_source_unit(source.id, kind="resource", label="Taken")
    audio, _text = _files(tmp_path)
    with pytest.raises(ResourceIngestionError, match="already exists"):
        service.resource_ingestion.ingest(ResourceIngestionRequest(
            ResourceType.AUDIO_ONLY, "Taken", existing_source_id=source.id,
            audio_path=audio,
        ))
    assert [unit.label for unit in service.corpus.list_source_units(source.id)] == [
        "Taken"
    ]
    assert service.corpus.list_processing_runs() == []


def test_attaches_audio_to_existing_text_resource_without_hierarchy_change(
    service: ProjectService, tmp_path: Path,
) -> None:
    _audio, text = _files(tmp_path)
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "Text", new_source_name="Course", text_path=text,
    ))
    audio = tmp_path / "later.wav"
    original = b"later audio evidence"
    audio.write_bytes(original)
    before_units = service.corpus.list_source_units(created.resource.source_id)
    before_tree = service.project_explorer.load_tree("Ingestion")

    attached = service.resource_ingestion.add_asset_to_resource(
        AssetAttachmentRequest(created.resource, AssetAttachmentType.AUDIO, audio)
    )

    assert attached.resource == created.resource
    assert service.corpus.list_source_units(created.resource.source_id) == before_units
    after_tree = service.project_explorer.load_tree("Ingestion")
    assert after_tree.project == before_tree.project
    overview = service.project_explorer.load_overview(created.resource)
    assert {asset.asset_kind for asset in overview.assets} == {"audio", "text"}
    assert audio.read_bytes() == original


@pytest.mark.parametrize(
    ("provenance", "kind"),
    ((TextProvenance.AUTHORITATIVE, "authoritative_source"),
     (TextProvenance.MACHINE_UNREVIEWED, "machine_transcript")),
)
def test_attaches_text_to_existing_audio_with_distinct_provenance(
    service: ProjectService, tmp_path: Path,
    provenance: TextProvenance, kind: str,
) -> None:
    audio, text = _files(tmp_path)
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_ONLY, provenance.value,
        new_source_name=f"Source {provenance.value}", audio_path=audio,
    ))
    unit_id = created.resource.source_unit_id
    content = text.read_bytes()
    attached = service.resource_ingestion.add_asset_to_resource(
        AssetAttachmentRequest(
            created.resource, AssetAttachmentType.TEXT, text, provenance
        )
    )
    representation = service.text_segments.get_text_representation(
        attached.text_representation_id
    )
    assert attached.resource.source_unit_id == unit_id
    assert representation.representation_kind == kind
    assert representation.content.encode() == content
    overview = service.project_explorer.load_overview(created.resource)
    assert len(overview.assets) == 2
    assert overview.utterances == ("你好", "再见")
    assert overview.transcript_provenance == (
        "authoritative"
        if provenance is TextProvenance.AUTHORITATIVE
        else "machine_unreviewed"
    )


def test_authoritative_transcript_is_preferred_over_machine_text(
    service: ProjectService, tmp_path: Path,
) -> None:
    audio, machine = _files(tmp_path)
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_ONLY, "Mixed", new_source_name="Source",
        audio_path=audio,
    ))
    service.resource_ingestion.add_asset_to_resource(AssetAttachmentRequest(
        created.resource, AssetAttachmentType.TEXT, machine,
        TextProvenance.MACHINE_UNREVIEWED,
    ))
    authoritative = tmp_path / "authoritative.txt"
    authoritative.write_text("权威一\n权威二", encoding="utf-8")
    service.resource_ingestion.add_asset_to_resource(AssetAttachmentRequest(
        created.resource, AssetAttachmentType.TEXT, authoritative,
        TextProvenance.AUTHORITATIVE,
    ))

    overview = service.project_explorer.load_overview(created.resource)

    assert overview.transcript_provenance == "authoritative"
    assert overview.utterances == ("权威一", "权威二")
    assert overview.reviewable_count == 0


def test_attachment_reuses_content_identity(
    service: ProjectService, tmp_path: Path,
) -> None:
    audio, _text = _files(tmp_path)
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_ONLY, "One", new_source_name="Source", audio_path=audio,
    ))
    copy = tmp_path / "copy.wav"
    copy.write_bytes(audio.read_bytes())
    attached = service.resource_ingestion.add_asset_to_resource(
        AssetAttachmentRequest(created.resource, AssetAttachmentType.AUDIO, copy)
    )
    assert attached.asset_id == created.asset_ids[0]
    assert len(service.corpus.list_asset_locations(attached.asset_id)) == 2


def test_failed_attachment_rolls_back_and_does_not_create_resource(
    service: ProjectService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lexbundler.persistence.sqlite.resource_ingestion_store as store_module

    source = service.corpus.create_source("Source")
    unit = service.corpus.create_source_unit(source.id, kind="resource", label="One")
    audio, _text = _files(tmp_path)
    original = store_module.dump_json

    def fail_at_completion(value):
        if isinstance(value, dict) and value.get("operation") == "add_asset_to_resource":
            raise RuntimeError("synthetic attachment failure")
        return original(value)

    monkeypatch.setattr(store_module, "dump_json", fail_at_completion)
    with pytest.raises(RuntimeError, match="attachment failure"):
        service.resource_ingestion.add_asset_to_resource(
            AssetAttachmentRequest(
                tree_resource_identity(source.id, unit.id),
                AssetAttachmentType.AUDIO, audio,
            )
        )
    assert service.corpus.list_source_units(source.id) == [unit]
    assert service.corpus.list_asset_bindings(source.id) == []
    assert service.corpus.list_processing_runs() == []


def tree_resource_identity(source_id: int, unit_id: int):
    from lexbundler.application.project_explorer_service import ResourceIdentity
    return ResourceIdentity(source_id, unit_id)
