"""Attach new evidence to one existing logical corpus resource."""

from pathlib import Path

from PySide6.QtCore import QThread, Slot
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from lexbundler.application.project_explorer_service import ResourceIdentity
from lexbundler.application.resource_ingestion_service import (
    AssetAttachmentRequest, AssetAttachmentResult, AssetAttachmentType,
    ResourceIngestionService, TextProvenance,
)
from lexbundler.ui.add_resource_dialog import IngestionWorker
from lexbundler.ui.style import apply_workbench_style


class AddAssetDialog(QDialog):
    def __init__(self, resource: ResourceIdentity, resource_label: str,
                 ingestion: ResourceIngestionService, parent=None) -> None:
        super().__init__(parent)
        self._resource = resource
        self._ingestion = ingestion
        self._thread: QThread | None = None
        self._worker: IngestionWorker | None = None
        self.result_attachment: AssetAttachmentResult | None = None
        self.setWindowTitle("Add Asset")
        self.setModal(True)
        self.resize(560, 300)

        heading = QLabel(f"Add evidence to {resource_label}")
        heading.setProperty("primaryValue", True)
        description = QLabel(
            "This attaches evidence to the selected resource. Its hierarchy and "
            "logical identity will not change."
        )
        description.setProperty("muted", True)
        description.setWordWrap(True)
        self.asset_type_combo = QComboBox(objectName="assetAttachmentTypeCombo")
        for asset_type in AssetAttachmentType:
            self.asset_type_combo.addItem(asset_type.label, asset_type)
        self.asset_type_combo.currentIndexChanged.connect(self._type_changed)
        self.path_edit = QLineEdit(objectName="assetAttachmentPathEdit")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        file_row = QWidget()
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.addWidget(self.path_edit, 1)
        file_layout.addWidget(browse)
        self.provenance_combo = QComboBox(objectName="assetTextProvenanceCombo")
        for provenance in TextProvenance:
            self.provenance_combo.addItem(provenance.label, provenance)
        self.form = QFormLayout()
        self.form.addRow("Asset type", self.asset_type_combo)
        self.form.addRow("File", file_row)
        self.form.addRow("Text provenance", self.provenance_combo)
        storage = QLabel("Referenced in original location")
        storage.setProperty("muted", True)
        self.form.addRow("Storage", storage)
        self.status_label = QLabel(objectName="assetAttachmentStatus")
        self.status_label.setProperty("muted", True)
        self.cancel_button = QPushButton("Cancel")
        self.import_button = QPushButton("Add Asset", objectName="confirmAddAssetButton")
        self.import_button.setProperty("primaryAction", True)
        self.cancel_button.clicked.connect(self.reject)
        self.import_button.clicked.connect(self._start_import)
        actions = QHBoxLayout()
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        actions.addWidget(self.import_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addLayout(self.form)
        layout.addWidget(self.status_label)
        layout.addStretch()
        layout.addLayout(actions)
        self._type_changed()
        apply_workbench_style(self)

    def _type_changed(self) -> None:
        self.form.setRowVisible(
            self.provenance_combo,
            self.asset_type_combo.currentData() is AssetAttachmentType.TEXT,
        )

    def _browse(self) -> None:
        file_filter = (
            "Text files (*.txt)"
            if self.asset_type_combo.currentData() is AssetAttachmentType.TEXT
            else "All Files (*)"
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Asset File", "", file_filter
        )
        if path:
            self.path_edit.setText(path)

    def build_request(self, *, validate_file=False) -> AssetAttachmentRequest:
        path_text = self.path_edit.text().strip()
        if not path_text:
            raise ValueError("Asset file is required.")
        path = Path(path_text)
        if validate_file and not path.is_file():
            raise ValueError(f"Asset path is not a regular file: {path}")
        return AssetAttachmentRequest(
            self._resource, self.asset_type_combo.currentData(), path,
            self.provenance_combo.currentData(),
        )

    def _start_import(self) -> None:
        if self._thread is not None:
            return
        try:
            request = self.build_request(validate_file=True)
        except ValueError as error:
            QMessageBox.warning(self, "Incomplete Asset", str(error))
            return
        self.status_label.setText("Adding asset…")
        self.cancel_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self._thread = QThread(self)
        self._worker = IngestionWorker(
            self._ingestion.add_asset_to_resource, request
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._succeeded)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    @Slot(object)
    def _succeeded(self, result: AssetAttachmentResult) -> None:
        self.result_attachment = result

    @Slot(str)
    def _failed(self, message: str) -> None:
        self.status_label.setText("Attachment failed.")
        self.cancel_button.setEnabled(True)
        self.import_button.setEnabled(True)
        QMessageBox.critical(self, "Could Not Add Asset", message)

    @Slot()
    def _thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        if self.result_attachment is not None:
            self.accept()

    def reject(self) -> None:
        if self._thread is None:
            super().reject()
