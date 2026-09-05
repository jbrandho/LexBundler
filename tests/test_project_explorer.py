from pathlib import Path

from PySide6.QtWidgets import QApplication

from lexbundler.application.pedagogical_review_service import PedagogicalReviewRequest
from lexbundler.application.project_service import ProjectService
from lexbundler.domain.text_segments import (
    AlignmentGraphSpec, AlignmentLayerSpec, AlignmentSegmentSpec,
)
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.main_window import MainWindow


def _project(tmp_path: Path):
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "explorer.lexbundler", name="Mandarin Corpus")
    source = project.corpus.create_source("Course")
    lesson = project.corpus.create_source_unit(
        source.id, kind="lesson", label="Lesson 1", sequence=0
    )
    text_one = project.corpus.create_source_unit(
        source.id, parent_id=lesson.id, kind="text", label="Dialogue", sequence=0
    )
    text_two = project.corpus.create_source_unit(
        source.id, parent_id=lesson.id, kind="text", label="Dialogue", sequence=1
    )
    return project, source, lesson, text_one, text_two


def test_explorer_maps_hierarchy_with_stable_duplicate_label_identity(
    tmp_path: Path,
) -> None:
    project, source, lesson, text_one, text_two = _project(tmp_path)

    tree = project.project_explorer.load_tree("Mandarin Corpus")

    source_node = tree.project.children[0]
    lesson_node = source_node.children[0]
    assert tree.project.label == "Mandarin Corpus"
    assert source_node.label == "Course"
    assert not source_node.is_resource
    assert lesson_node.label == "Lesson 1"
    assert not lesson_node.is_resource
    assert [item.label for item in lesson_node.children] == ["Dialogue", "Dialogue"]
    assert [item.key for item in lesson_node.children] == [
        ("unit", text_one.id), ("unit", text_two.id)
    ]
    assert all(item.is_resource for item in lesson_node.children)
    assert tree.resource_count == 2
    assert source_node.resource.source_id == source.id
    assert lesson_node.resource.source_unit_id == lesson.id


def test_empty_project_has_no_resources(tmp_path: Path) -> None:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "empty.lexbundler", name="Empty")

    tree = project.project_explorer.load_tree("Empty")

    assert tree.project.children == ()
    assert tree.resource_count == 0


def test_resource_overview_uses_existing_evidence_and_review_state(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    project, source, _lesson, text_one, _text_two = _project(tmp_path)
    transcript_file = tmp_path / "dialogue.txt"
    transcript_file.write_text("你好\n再见", encoding="utf-8")
    transcript = project.transcript_imports.import_utf8(
        transcript_file, source_id=source.id, source_unit_id=text_one.id
    ).graph
    audio_file = tmp_path / "dialogue.wav"
    audio_file.write_bytes(b"audio")
    audio = project.corpus.register_local_asset(audio_file, asset_kind="audio")
    project.corpus.bind_asset_to_source_unit(
        source.id, text_one.id, audio.id, role="source_audio"
    )
    run = project.corpus.start_processing_run("import", tool_name="MFA")
    alignment = project.text_segments.create_alignment_graph(AlignmentGraphSpec(
        source.id, text_one.id, transcript.representation.id, audio.id, None, run.id,
        "authoritative_alignment", "aligned_source",
        (AlignmentLayerSpec(
            "MFA words", "forced_alignment", "alignment_word", {"tier": "words"},
            (AlignmentSegmentSpec(0, "你好", 100, 400, 0, 2),),
        ),),
    ))
    project.corpus.finish_processing_run(run.id, status="succeeded")
    transcript_span = project.text_segments.list_segment_text_spans(
        transcript.segments[0].id
    )[0]
    project.pedagogical_reviews.approve(PedagogicalReviewRequest(
        transcript.segments[0].id, transcript_span.id, audio.id,
        80, 450, alignment.layers[0].id, 100, 400, True,
    ))

    overview = project.project_explorer.load_overview(
        tree_resource(project, text_one.id)
    )

    assert overview.breadcrumb == ("Course", "Lesson 1", "Dialogue")
    assert overview.utterances == ("你好", "再见")
    assert overview.approved_count == 1
    assert overview.reviewable_count == 1
    assert overview.alignments[0].tier == "words"
    assert overview.alignments[0].item_count == 1
    assert any(asset.label == "dialogue.wav" for asset in overview.assets)
    assert any(item.process_type == "review" for item in overview.processing_history)
    window = MainWindow(project)
    assert window.workspace.continue_review_button.isEnabled()
    window.workspace.continue_review_button.click()
    assert window.workspace.tabs.currentWidget() is window.review_widget
    window.close()


def tree_resource(project: ProjectService, unit_id: int):
    tree = project.project_explorer.load_tree(project.current_project.name)
    stack = [tree.project]
    while stack:
        node = stack.pop()
        if node.key == ("unit", unit_id):
            return node.resource
        stack.extend(reversed(node.children))
    raise AssertionError("resource node not found")
