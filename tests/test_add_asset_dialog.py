from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from lexbundler.application.project_explorer_service import ResourceIdentity
from lexbundler.application.project_service import ProjectService
from lexbundler.application.resource_ingestion_service import AssetAttachmentType
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.add_asset_dialog import AddAssetDialog


def test_add_asset_dialog_attaches_to_fixed_resource(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(tmp_path / "asset-dialog.lexbundler", name="Dialog")
    source = service.corpus.create_source("Source")
    unit = service.corpus.create_source_unit(source.id, kind="resource", label="Text")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    dialog = AddAssetDialog(
        ResourceIdentity(source.id, unit.id), unit.label,
        service.resource_ingestion,
    )
    dialog.path_edit.setText(str(audio))
    loop = QEventLoop()
    dialog.finished.connect(loop.quit)
    QTimer.singleShot(5000, loop.quit)
    dialog._start_import()
    loop.exec()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_attachment.resource == ResourceIdentity(source.id, unit.id)
    assert service.corpus.list_source_units(source.id) == [unit]
    service.close_project()


def test_add_asset_dialog_text_type_exposes_provenance(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(tmp_path / "asset-form.lexbundler", name="Dialog")
    source = service.corpus.create_source("Source")
    unit = service.corpus.create_source_unit(source.id, kind="resource", label="Audio")
    dialog = AddAssetDialog(
        ResourceIdentity(source.id, unit.id), unit.label,
        service.resource_ingestion,
    )
    dialog.asset_type_combo.setCurrentIndex(
        dialog.asset_type_combo.findData(AssetAttachmentType.TEXT)
    )
    assert dialog.form.isRowVisible(dialog.provenance_combo)
    dialog.reject()
    assert service.corpus.list_asset_bindings(source.id) == []
    service.close_project()
