"""Focused three-step Add Resource workflow."""

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.resource_ingestion_service import (
    ResourceIngestionRequest, ResourceIngestionResult, ResourceIngestionService,
    ResourceType, TextProvenance,
)
from lexbundler.ui.style import apply_workbench_style


class IngestionWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation, request) -> None:
        super().__init__()
        self._operation = operation
        self._request = request

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation(self._request))
        except Exception as error:
            self.failed.emit(str(error) or "Resource import failed.")
        finally:
            self.finished.emit()


class AddResourceDialog(QDialog):
    def __init__(self, corpus: CorpusService,
                 ingestion: ResourceIngestionService, parent=None) -> None:
        super().__init__(parent)
        self._corpus = corpus
        self._ingestion = ingestion
        self._thread: QThread | None = None
        self._worker: IngestionWorker | None = None
        self.result_resource: ResourceIngestionResult | None = None
        self.setWindowTitle("Add Resource")
        self.setModal(True)
        self.resize(650, 520)

        self.step_label = QLabel(objectName="addResourceStepLabel")
        self.step_label.setProperty("sectionHeading", True)
        self.pages = QStackedWidget(objectName="addResourcePages")
        self.pages.addWidget(self._build_type_page())
        self.pages.addWidget(self._build_details_page())
        self.pages.addWidget(self._build_review_page())

        self.back_button = QPushButton("Back", objectName="addResourceBackButton")
        self.next_button = QPushButton("Next", objectName="addResourceNextButton")
        self.next_button.setProperty("primaryAction", True)
        self.cancel_button = QPushButton("Cancel", objectName="addResourceCancelButton")
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        layout.addWidget(self.step_label)
        layout.addWidget(self.pages, 1)
        layout.addLayout(buttons)
        self._populate_sources()
        self._type_changed()
        self._update_navigation()
        apply_workbench_style(self)

    def _build_type_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("What kind of resource are you adding?")
        heading.setProperty("primaryValue", True)
        layout.addWidget(heading)
        self.type_group = QButtonGroup(self)
        choices = (
            (ResourceType.AUDIO_TRANSCRIPT, "Audio + Transcript",
             "Spoken material with corresponding text. Example: HSK textbook dialogue."),
            (ResourceType.AUDIO_ONLY, "Audio Only",
             "Spoken material without a trusted transcript. Example: podcast/course audio."),
            (ResourceType.TEXT_ONLY, "Text Only",
             "Textual corpus material without corresponding audio."),
        )
        for index, (kind, title, description) in enumerate(choices):
            frame = QFrame()
            frame.setProperty("choicePanel", True)
            row = QHBoxLayout(frame)
            radio = QRadioButton(title, objectName=f"resourceType{kind.name.title()}")
            radio.setProperty("resourceType", kind.value)
            detail = QLabel(description)
            detail.setProperty("muted", True)
            detail.setWordWrap(True)
            row.addWidget(radio)
            row.addWidget(detail, 1)
            layout.addWidget(frame)
            self.type_group.addButton(radio, index)
            radio.toggled.connect(self._type_changed)
            if index == 0:
                radio.setChecked(True)
        layout.addStretch()
        return page

    def _build_details_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.details_form = form
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.source_combo = QComboBox(objectName="resourceSourceCombo")
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.new_source_edit = QLineEdit(objectName="newSourceNameEdit")
        self.new_source_edit.setPlaceholderText("New source name")
        self.parent_combo = QComboBox(objectName="resourceParentCombo")
        parent_help = QLabel(
            "Choose a hierarchy container. To add evidence to an existing resource, "
            "use Add Asset from that resource's workspace."
        )
        parent_help.setProperty("muted", True)
        parent_help.setWordWrap(True)
        self.parent_path_edit = QLineEdit(objectName="newParentPathEdit")
        self.parent_path_edit.setPlaceholderText("Optional: Lesson 1 / Section A")
        self.resource_name_edit = QLineEdit(objectName="resourceNameEdit")
        self.resource_name_edit.setPlaceholderText("Resource name")
        form.addRow("Source", self.source_combo)
        form.addRow("New source", self.new_source_edit)
        form.addRow("Existing parent", self.parent_combo)
        form.addRow("", parent_help)
        form.addRow("New parent path", self.parent_path_edit)
        form.addRow("Resource name", self.resource_name_edit)

        self.audio_row, self.audio_path_edit = self._file_row("Choose Audio File")
        self.text_row, self.text_path_edit = self._file_row(
            "Choose Text File", "Text files (*.txt)"
        )
        self.provenance_combo = QComboBox(objectName="textProvenanceCombo")
        for provenance in TextProvenance:
            self.provenance_combo.addItem(provenance.label, provenance)
        form.addRow("Audio file", self.audio_row)
        form.addRow("Text file", self.text_row)
        form.addRow("Text provenance", self.provenance_combo)
        storage = QLabel("Files will be referenced in their original locations.")
        storage.setProperty("muted", True)
        form.addRow("Storage", storage)
        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Review & Import")
        heading.setProperty("primaryValue", True)
        self.review_summary = QLabel(objectName="resourceImportSummary")
        self.review_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.review_summary.setWordWrap(True)
        self.import_status = QLabel(objectName="resourceImportStatus")
        self.import_status.setProperty("muted", True)
        layout.addWidget(heading)
        layout.addWidget(self.review_summary)
        layout.addWidget(self.import_status)
        layout.addStretch()
        return page

    def _file_row(self, caption: str, file_filter: str = "All Files (*)") -> tuple[QWidget, QLineEdit]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        button = QPushButton("Browse…")
        button.clicked.connect(lambda: self._browse(edit, caption, file_filter))
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return row, edit

    def _browse(self, target: QLineEdit, caption: str, file_filter: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, caption, "", file_filter)
        if path:
            target.setText(path)

    def _populate_sources(self) -> None:
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for source in self._corpus.list_sources():
            self.source_combo.addItem(source.name, source.id)
        self.source_combo.addItem("Create new source…", None)
        self.source_combo.blockSignals(False)
        self._source_changed()

    def _source_changed(self) -> None:
        source_id = self.source_combo.currentData()
        creating = source_id is None
        self.new_source_edit.setEnabled(creating)
        self.parent_combo.setEnabled(not creating)
        self.parent_combo.clear()
        self.parent_combo.addItem("Source root", None)
        if source_id is not None:
            units = list(self._ingestion.list_container_units(source_id))
            by_id = {unit.id: unit for unit in units}
            for unit in units:
                labels = [unit.label]
                parent_id = unit.parent_id
                seen = {unit.id}
                while parent_id is not None and parent_id not in seen:
                    seen.add(parent_id)
                    parent = by_id.get(parent_id)
                    if parent is None:
                        break
                    labels.append(parent.label)
                    parent_id = parent.parent_id
                self.parent_combo.addItem(" / ".join(reversed(labels)), unit.id)

    def _selected_type(self) -> ResourceType:
        button = self.type_group.checkedButton()
        return ResourceType(button.property("resourceType"))

    def _type_changed(self) -> None:
        if not hasattr(self, "audio_row"):
            return
        resource_type = self._selected_type()
        has_audio = resource_type is not ResourceType.TEXT_ONLY
        has_text = resource_type is not ResourceType.AUDIO_ONLY
        self.details_form.setRowVisible(self.audio_row, has_audio)
        self.details_form.setRowVisible(self.text_row, has_text)
        self.details_form.setRowVisible(self.provenance_combo, has_text)

    def _back(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._update_navigation()

    def _next(self) -> None:
        page = self.pages.currentIndex()
        if page == 0:
            self.pages.setCurrentIndex(1)
        elif page == 1:
            try:
                request = self.build_request(validate_files=True)
            except ValueError as error:
                QMessageBox.warning(self, "Incomplete Resource", str(error))
                return
            self._update_review(request)
            self.pages.setCurrentIndex(2)
        else:
            self._start_import()
        self._update_navigation()

    def build_request(self, *, validate_files: bool = False) -> ResourceIngestionRequest:
        resource_type = self._selected_type()
        name = self.resource_name_edit.text().strip()
        if not name:
            raise ValueError("Resource name is required.")
        source_id = self.source_combo.currentData()
        new_source = self.new_source_edit.text().strip() or None
        if source_id is None and new_source is None:
            raise ValueError("New source name is required.")
        parent_labels = tuple(
            part.strip() for part in self.parent_path_edit.text().split("/")
            if part.strip()
        )
        audio_text = self.audio_path_edit.text().strip()
        text_value = self.text_path_edit.text().strip()
        audio = (
            Path(audio_text)
            if resource_type is not ResourceType.TEXT_ONLY and audio_text else None
        )
        text = (
            Path(text_value)
            if resource_type is not ResourceType.AUDIO_ONLY and text_value else None
        )
        if validate_files:
            if resource_type is not ResourceType.TEXT_ONLY and audio is None:
                raise ValueError("Audio file is required.")
            if resource_type is not ResourceType.AUDIO_ONLY and text is None:
                raise ValueError("Text file is required.")
            for label, path in (("Audio", audio), ("Text", text)):
                if path is None:
                    continue
                if not path.is_file():
                    raise ValueError(f"{label} path is not a regular file: {path}")
        return ResourceIngestionRequest(
            resource_type=resource_type,
            resource_name=name,
            existing_source_id=source_id,
            new_source_name=new_source if source_id is None else None,
            existing_parent_unit_id=(
                self.parent_combo.currentData() if source_id is not None else None
            ),
            new_parent_labels=parent_labels,
            audio_path=audio,
            text_path=text,
            text_provenance=self.provenance_combo.currentData(),
        )

    def _update_review(self, request: ResourceIngestionRequest) -> None:
        source = request.new_source_name or self.source_combo.currentText()
        hierarchy = [source]
        if request.existing_parent_unit_id is not None:
            hierarchy.append(self.parent_combo.currentText())
        hierarchy.extend(request.new_parent_labels)
        hierarchy.append(request.resource_name)
        lines = ["RESOURCE", "  › ".join(hierarchy), "", "TYPE",
                 request.resource_type.label]
        if request.audio_path is not None:
            lines.extend(("", "AUDIO", request.audio_path.name, str(request.audio_path),
                          "Referenced in original location"))
        if request.text_path is not None:
            lines.extend(("", "TRANSCRIPT", request.text_path.name,
                          str(request.text_path), request.text_provenance.label,
                          "Referenced in original location"))
        lines.extend(("", "PROCESSING", "No processing will be run during import."))
        self.review_summary.setText("\n".join(lines))
        self.import_status.clear()

    def _start_import(self) -> None:
        if self._thread is not None:
            return
        try:
            request = self.build_request(validate_files=True)
        except ValueError as error:
            QMessageBox.warning(self, "Incomplete Resource", str(error))
            return
        self.import_status.setText("Importing resource…")
        self._set_busy(True)
        self._thread = QThread(self)
        self._worker = IngestionWorker(self._ingestion.ingest, request)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.succeeded.connect(self._import_succeeded)
        self._worker.failed.connect(self._import_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    @Slot(object)
    def _import_succeeded(self, result: ResourceIngestionResult) -> None:
        self.result_resource = result
        self.import_status.setText("Resource imported.")

    @Slot(str)
    def _import_failed(self, message: str) -> None:
        self.import_status.setText("Import failed.")
        self._set_busy(False)
        QMessageBox.critical(self, "Could Not Import Resource", message)

    @Slot()
    def _thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        if self.result_resource is not None:
            self.accept()

    def _set_busy(self, busy: bool) -> None:
        self.back_button.setEnabled(not busy)
        self.next_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)

    def _update_navigation(self) -> None:
        page = self.pages.currentIndex()
        self.step_label.setText(
            ("1 of 3  ·  TYPE", "2 of 3  ·  DETAILS & FILES",
             "3 of 3  ·  REVIEW & IMPORT")[page]
        )
        self.back_button.setEnabled(page > 0)
        self.next_button.setText("Import Resource" if page == 2 else "Next")

    def reject(self) -> None:
        if self._thread is None:
            super().reject()
