from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QHeaderView, QProgressBar, QTabWidget, QTreeView,
)

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.main_window import MainWindow


def test_main_window_can_be_constructed(qapplication: QApplication) -> None:
    window = MainWindow(ProjectService(SQLiteProjectStoreFactory()))

    assert window.windowTitle() == "LexBundler"
    assert window.centralWidget() is not None
    assert window.workspace.stack.currentIndex() == 0
    assert window.workspace.empty_state.text() == (
        "Open a project to browse corpus resources."
    )

    window.close()


def test_window_close_explicitly_shuts_down_playback(
    qapplication: QApplication,
) -> None:
    window = MainWindow(ProjectService(SQLiteProjectStoreFactory()))
    playback = window.review_widget._playback
    original_shutdown = playback.shutdown
    calls = []

    def recording_shutdown():
        calls.append("shutdown")
        original_shutdown()

    playback.shutdown = recording_shutdown

    window.close()

    assert calls == ["shutdown"]


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


def test_explorer_workspace_populates_selects_and_clears(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    service.create_project(tmp_path / "workbench.lexbundler", name="Workbench")
    source = service.corpus.create_source("Source")
    first = service.corpus.create_source_unit(
        source.id, kind="resource", label="Same label", sequence=0
    )
    service.corpus.create_source_unit(
        source.id, kind="resource", label="Same label", sequence=1
    )
    transcript = tmp_path / "dialogue.txt"
    transcript.write_text("one\ntwo", encoding="utf-8")
    service.transcript_imports.import_utf8(
        transcript, source_id=source.id, source_unit_id=first.id
    )

    window._refresh_project_state()

    tree = window.findChild(QTreeView, "corpusExplorerTree")
    tabs = window.findChild(QTabWidget, "resourceTabs")
    assert not tree.isHidden()
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Overview", "Transcript", "Alignment", "Review", "Assets"
    ]
    assert window.workspace.breadcrumb.text() == "Source  ›  Same label"
    assert window.workspace.transcript_list.count() == 2
    assert window.workspace.transcript_model.index(0, 0).data() == "1"
    assert window.workspace.transcript_model.index(1, 1).data() == "two"
    assert window.workspace.transcript_summary.text() == "Authoritative · 2 utterances"
    assert window.workspace.transcript_card.primary.text() == "Authoritative"
    assert window.findChild(QProgressBar, "overviewReviewProgress").maximum() == 2
    assert window.workspace.assets_table.horizontalHeader().sectionResizeMode(4) == (
        QHeaderView.ResizeMode.Stretch
    )
    assert window.workspace.alignment_table.horizontalHeader().sectionResizeMode(2) == (
        QHeaderView.ResizeMode.ResizeToContents
    )
    assert window.review_widget._selected_source_unit_id == first.id

    window.workspace.continue_review_button.click()
    assert tabs.currentWidget() is window.review_widget

    window._close_project()

    assert window.explorer.model.rowCount() == 0
    assert window.workspace._resource is None
    assert window.workspace.stack.currentIndex() == 0
    assert window.workspace.breadcrumb.text().startswith("Open a project")
    window.close()


def test_empty_project_shows_resource_empty_state(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    service.create_project(tmp_path / "empty.lexbundler", name="Empty")

    window._refresh_project_state()

    assert "No resources yet" in window.explorer.empty_label.text()
    assert not window.explorer.tree.isVisible()
    assert window.workspace._resource is None
    assert window.workspace.stack.currentIndex() == 0
    assert window.workspace.empty_state.text().startswith("No resources yet")
    window.close()
