from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QDialog, QHeaderView, QProgressBar, QTabWidget, QTreeView,
)

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.main_window import MainWindow
from lexbundler.application.resource_ingestion_service import (
    AssetAttachmentRequest, AssetAttachmentType, ResourceIngestionRequest,
    ResourceType, TextProvenance,
)


def test_main_window_can_be_constructed(qapplication: QApplication) -> None:
    window = MainWindow(ProjectService(SQLiteProjectStoreFactory()))

    assert window.windowTitle() == "LexBundler"
    assert window.centralWidget() is not None
    assert window.workspace.stack.currentIndex() == 0
    assert window.workspace.empty_state.text() == (
        "Open a project to browse corpus resources."
    )
    assert not window.workspace.add_asset_button.isEnabled()
    window._add_asset()

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
    assert not window.explorer.add_button.isEnabled()

    service.create_project(tmp_path / "Mandarin.lexbundler", name="Mandarin Corpus")
    window._refresh_project_state()

    assert window.windowTitle() == "LexBundler — Mandarin Corpus"
    assert not new_action.isEnabled()
    assert not open_action.isEnabled()
    assert close_action.isEnabled()
    assert window.explorer.add_button.isEnabled()

    close_action.trigger()
    assert service.current_project is None
    assert window.windowTitle() == "LexBundler"
    assert new_action.isEnabled()
    assert open_action.isEnabled()
    assert not close_action.isEnabled()
    assert not window.explorer.add_button.isEnabled()

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
    progress = window.findChild(QProgressBar, "overviewReviewProgress")
    assert progress.isHidden()
    assert not window.workspace.continue_review_button.isEnabled()
    assert window.workspace.assets_table.horizontalHeader().sectionResizeMode(4) == (
        QHeaderView.ResizeMode.Stretch
    )
    assert window.workspace.alignment_table.horizontalHeader().sectionResizeMode(2) == (
        QHeaderView.ResizeMode.ResizeToContents
    )
    assert window.review_widget._selected_source_unit_id == first.id

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


def test_machine_transcript_overview_is_not_labeled_authoritative(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    service.create_project(tmp_path / "machine.lexbundler", name="Machine")
    text = tmp_path / "machine.txt"
    text.write_text("unreviewed text", encoding="utf-8")
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "Machine text", new_source_name="Source",
        text_path=text,
        text_provenance=TextProvenance.MACHINE_UNREVIEWED,
    ))

    window._refresh_project_state(select_resource=created.resource)

    assert window.workspace.transcript_card.primary.text() == "Machine transcript"
    assert window.workspace.transcript_card.status.text() == "Needs review"
    assert window.workspace.transcript_summary.text() == (
        "Machine transcript · Needs review"
    )
    assert window.workspace.transcript_model.rowCount() == 1
    assert window.workspace.transcript_model.index(0, 1).data() == "unreviewed text"
    assert window.review_widget.transcript_model.rowCount() == 0
    assert not window.workspace.continue_review_button.isEnabled()
    window.close()


def test_audio_only_resource_shows_no_transcript_available(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    service.create_project(tmp_path / "audio.lexbundler", name="Audio")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.AUDIO_ONLY, "Audio only", new_source_name="Source",
        audio_path=audio,
    ))

    window._refresh_project_state(select_resource=created.resource)

    assert window.workspace.transcript_empty.text() == "No transcript available"
    assert window.workspace.transcript_model.rowCount() == 0
    window.close()


def test_successful_add_resource_refreshes_selects_and_opens_workspace(
    qapplication: QApplication, tmp_path: Path, monkeypatch,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    service.create_project(tmp_path / "gui-import.lexbundler", name="GUI")
    text = tmp_path / "text.txt"
    text.write_text("one\ntwo", encoding="utf-8")
    imported = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "New Resource", new_source_name="New Source",
        text_path=text,
    ))

    class AcceptedDialog:
        result_resource = imported

        def __init__(self, *_args):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    import lexbundler.ui.main_window as main_window_module
    monkeypatch.setattr(main_window_module, "AddResourceDialog", AcceptedDialog)
    window._add_resource()

    assert window.workspace._resource == imported.resource
    assert window.workspace.title.text() == "New Resource"
    assert window.workspace.tabs.currentWidget() is window.workspace.overview_tab
    assert window.explorer.tree.currentIndex().data(
        window.explorer.model.ResourceRole
    ) == imported.resource
    window.close()


def test_successful_add_asset_keeps_resource_selected_and_refreshes_workspace(
    qapplication: QApplication, tmp_path: Path, monkeypatch,
) -> None:
    service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(service)
    service.create_project(tmp_path / "gui-asset.lexbundler", name="GUI")
    text = tmp_path / "text.txt"
    text.write_text("one\ntwo", encoding="utf-8")
    created = service.resource_ingestion.ingest(ResourceIngestionRequest(
        ResourceType.TEXT_ONLY, "Resource", new_source_name="Source", text_path=text,
    ))
    window._refresh_project_state(select_resource=created.resource)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio evidence")
    attached = service.resource_ingestion.add_asset_to_resource(
        AssetAttachmentRequest(created.resource, AssetAttachmentType.AUDIO, audio)
    )

    class AcceptedDialog:
        result_attachment = attached

        def __init__(self, *_args):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

    import lexbundler.ui.main_window as main_window_module
    monkeypatch.setattr(main_window_module, "AddAssetDialog", AcceptedDialog)
    window._add_asset()

    assert window.workspace.current_resource == created.resource
    assert window.explorer.model.resource_index(created.resource).isValid()
    assert service.project_explorer.load_tree("GUI").resource_count == 1
    assert window.workspace.assets_table.rowCount() == 2
    assert window.workspace.transcript_list.count() == 2
    window.close()
