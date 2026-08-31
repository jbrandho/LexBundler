"""Minimal dialog for collecting new-project metadata and destination."""

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

PROJECT_FILTER = "LexBundler Projects (*.lexbundler)"


class NewProjectDialog(QDialog):
    """Collect the small amount of metadata required to create a project."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New LexBundler Project")

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("projectNameEdit")
        self.language_name_edit = QLineEdit()
        self.language_name_edit.setObjectName("primaryLanguageNameEdit")
        self.language_tag_edit = QLineEdit()
        self.language_tag_edit.setObjectName("primaryLanguageTagEdit")
        self.destination_edit = QLineEdit()
        self.destination_edit.setObjectName("projectDestinationEdit")

        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._choose_destination)
        destination_layout = QHBoxLayout()
        destination_layout.addWidget(self.destination_edit)
        destination_layout.addWidget(browse_button)

        form = QFormLayout()
        form.addRow("Project name:", self.name_edit)
        form.addRow("Primary language name:", self.language_name_edit)
        form.addRow("Primary language tag:", self.language_tag_edit)
        form.addRow("Destination:", destination_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @property
    def destination(self) -> Path:
        return Path(self.destination_edit.text().strip())

    @property
    def project_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def primary_language_name(self) -> str | None:
        return self.language_name_edit.text().strip() or None

    @property
    def primary_language_tag(self) -> str | None:
        return self.language_tag_edit.text().strip() or None

    def accept(self) -> None:
        if not self.project_name:
            QMessageBox.warning(self, "Missing Project Name", "Enter a project name.")
            return
        if not self.destination_edit.text().strip():
            QMessageBox.warning(
                self, "Missing Destination", "Choose a destination project file."
            )
            return
        super().accept()

    def _choose_destination(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Create LexBundler Project",
            self.destination_edit.text(),
            PROJECT_FILTER,
        )
        if filename:
            self.destination_edit.setText(filename)

