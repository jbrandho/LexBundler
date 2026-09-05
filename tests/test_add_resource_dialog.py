from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from lexbundler.application.project_service import ProjectService
from lexbundler.application.resource_ingestion_service import ResourceType
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.add_resource_dialog import AddResourceDialog


def _dialog(tmp_path: Path) -> tuple[ProjectService, AddResourceDialog]:
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(tmp_path / "dialog.lexbundler", name="Dialog")
    return service, AddResourceDialog(service.corpus, service.resource_ingestion)


def test_three_steps_preserve_details_and_build_review(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service, dialog = _dialog(tmp_path)
    text = tmp_path / "text.txt"
    text.write_text("你好", encoding="utf-8")
    dialog.type_group.button(2).setChecked(True)
    dialog._next()
    dialog.new_source_edit.setText("Texts")
    dialog.parent_path_edit.setText("Course / Lesson 1")
    dialog.resource_name_edit.setText("Text 1")
    dialog.text_path_edit.setText(str(text))
    dialog._next()

    assert dialog.pages.currentIndex() == 2
    assert dialog.next_button.text() == "Import Resource"
    assert "Texts  › Course  › Lesson 1  › Text 1" in dialog.review_summary.text()
    assert "Referenced in original location" in dialog.review_summary.text()
    assert "No processing will be run" in dialog.review_summary.text()

    dialog._back()
    assert dialog.resource_name_edit.text() == "Text 1"
    dialog.reject()
    assert service.corpus.list_sources() == []
    service.close_project()


def test_dialog_worker_imports_without_blocking_ui_contract(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service, dialog = _dialog(tmp_path)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    dialog.type_group.button(1).setChecked(True)
    dialog.source_combo.setCurrentIndex(dialog.source_combo.count() - 1)
    dialog.new_source_edit.setText("Recordings")
    dialog.resource_name_edit.setText("Field recording")
    dialog.audio_path_edit.setText(str(audio))
    dialog.pages.setCurrentIndex(2)
    dialog._update_navigation()

    loop = QEventLoop()
    dialog.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    dialog._start_import()
    loop.exec()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_resource is not None
    assert service.corpus.get_source_unit(
        dialog.result_resource.resource.source_unit_id
    ).label == "Field recording"
    service.close_project()


def test_existing_resources_are_not_offered_as_new_resource_parents(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(tmp_path / "parents.lexbundler", name="Parents")
    source = service.corpus.create_source("Source")
    container = service.corpus.create_source_unit(
        source.id, kind="lesson", label="Lesson"
    )
    resource = service.corpus.create_source_unit(
        source.id, parent_id=container.id, kind="resource", label="Text"
    )
    legacy = service.corpus.create_source_unit(
        source.id, parent_id=container.id, kind="dialogue", label="Legacy Resource"
    )
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("evidence", encoding="utf-8")
    asset = service.corpus.register_local_asset(evidence)
    service.corpus.bind_asset_to_source_unit(source.id, legacy.id, asset.id)
    dialog = AddResourceDialog(service.corpus, service.resource_ingestion)
    assert dialog.parent_combo.findData(container.id) >= 0
    assert dialog.parent_combo.findData(resource.id) == -1
    assert dialog.parent_combo.findData(legacy.id) == -1
    dialog.reject()
    service.close_project()
