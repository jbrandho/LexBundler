from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.main_window import MainWindow


def test_main_window_can_be_constructed(qapplication: QApplication) -> None:
    window = MainWindow(ProjectService(SQLiteProjectStoreFactory()))

    assert window.windowTitle() == "LexBundler"
    assert window.centralWidget() is not None

    window.close()


def test_file_actions_and_project_state(
    qapplication: QApplication, tmp_path: Path
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    new_action = window.findChild(QAction, "newProjectAction")
    open_action = window.findChild(QAction, "openProjectAction")
    close_action = window.findChild(QAction, "closeProjectAction")
    quit_action = window.findChild(QAction, "quitAction")

    assert all((new_action, open_action, close_action, quit_action))
    assert new_action.isEnabled()
    assert open_action.isEnabled()
    assert not close_action.isEnabled()

    service.create_project(tmp_path / "Mandarin.lexbundler", name="Mandarin Corpus")
    window._refresh_project_state()

    assert window.windowTitle() == "LexBundler — Mandarin Corpus"
    assert not new_action.isEnabled()
    assert not open_action.isEnabled()
    assert close_action.isEnabled()

    close_action.trigger()
    assert service.current_project is None
    assert window.windowTitle() == "LexBundler"
    assert new_action.isEnabled()
    assert open_action.isEnabled()
    assert not close_action.isEnabled()

    window.close()

