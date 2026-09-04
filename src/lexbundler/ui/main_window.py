"""LexBundler's main application window and thin lifecycle UI."""

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
)
from PySide6.QtCore import Qt

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import ProjectError
from lexbundler.ui.project_dialog import NewProjectDialog, PROJECT_FILTER
from lexbundler.ui.project_explorer import CorpusExplorerPane
from lexbundler.ui.resource_workspace import ResourceWorkspace
from lexbundler.ui.style import apply_workbench_style


class MainWindow(QMainWindow):
    """Top-level window for the LexBundler desktop application."""

    def __init__(self, project_service: ProjectService) -> None:
        super().__init__()
        self._project_service = project_service
        self.setWindowTitle("LexBundler")
        self.resize(1200, 750)

        self._new_project_action = QAction("New Project...", self)
        self._new_project_action.setObjectName("newProjectAction")
        self._new_project_action.triggered.connect(self._new_project)
        self._open_project_action = QAction("Open Project...", self)
        self._open_project_action.setObjectName("openProjectAction")
        self._open_project_action.triggered.connect(self._open_project)
        self._close_project_action = QAction("Close Project", self)
        self._close_project_action.setObjectName("closeProjectAction")
        self._close_project_action.triggered.connect(self._close_project)
        quit_action = QAction("Quit", self)
        quit_action.setObjectName("quitAction")
        quit_action.triggered.connect(self.close)

        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self._new_project_action)
        file_menu.addAction(self._open_project_action)
        file_menu.addAction(self._close_project_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        self.explorer = CorpusExplorerPane()
        self.workspace = ResourceWorkspace(
            project_service.project_explorer,
            project_service.alignment_review,
            project_service.pedagogical_reviews,
        )
        self.review_widget = self.workspace.review_tab
        self.explorer.resourceSelected.connect(self.workspace.set_resource)
        self.workbench = QSplitter(Qt.Orientation.Horizontal)
        self.workbench.setObjectName("corpusWorkbench")
        self.workbench.addWidget(self.explorer)
        self.workbench.addWidget(self.workspace)
        self.workbench.setStretchFactor(0, 0)
        self.workbench.setStretchFactor(1, 1)
        self.workbench.setChildrenCollapsible(False)
        self.workbench.setHandleWidth(1)
        self.workbench.setSizes([270, 930])
        self.setCentralWidget(self.workbench)
        apply_workbench_style(self)
        self._refresh_project_state()

    def _new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._project_service.create_project(
                dialog.destination,
                name=dialog.project_name,
                primary_language_tag=dialog.primary_language_tag,
                primary_language_name=dialog.primary_language_name,
            )
        except ProjectError as error:
            self._show_project_error("Could Not Create Project", error)
            return
        self._refresh_project_state()

    def _open_project(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open LexBundler Project", "", PROJECT_FILTER
        )
        if not filename:
            return
        try:
            self._project_service.open_project(filename)
        except ProjectError as error:
            self._show_project_error("Could Not Open Project", error)
            return
        self._refresh_project_state()

    def _close_project(self) -> None:
        self._project_service.close_project()
        self._refresh_project_state()

    def _refresh_project_state(self) -> None:
        project = self._project_service.current_project
        is_open = project is not None
        self._new_project_action.setEnabled(not is_open)
        self._open_project_action.setEnabled(not is_open)
        self._close_project_action.setEnabled(is_open)
        if project is None:
            self.setWindowTitle("LexBundler")
            self.statusBar().clearMessage()
            self.explorer.clear()
            self.workspace.clear()
        else:
            self.setWindowTitle(f"LexBundler — {project.name}")
            self.statusBar().showMessage(project.name)
            tree = self._project_service.project_explorer.load_tree(project.name)
            self.explorer.populate(tree)
            if tree.resource_count == 0:
                self.workspace.show_empty_project()
            self.statusBar().showMessage(
                f"{project.name} — {tree.resource_count} resources"
            )

    def _show_project_error(self, title: str, error: ProjectError) -> None:
        QMessageBox.critical(self, title, str(error))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        self.workspace.shutdown()
        self._project_service.close_project()
        event.accept()
