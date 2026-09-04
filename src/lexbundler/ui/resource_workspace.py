"""Tabbed workspace for one Explorer-selected corpus resource."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHeaderView, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QScrollArea, QStackedLayout, QTabWidget,
    QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from lexbundler.application.alignment_review_service import AlignmentReviewService
from lexbundler.application.pedagogical_review_service import PedagogicalReviewService
from lexbundler.application.project_explorer_service import (
    ProjectExplorerService, ResourceIdentity, ResourceOverview,
)
from lexbundler.ui.alignment_review_widget import AlignmentReviewWidget


class TranscriptTableModel(QAbstractTableModel):
    HEADERS = ("#", "Utterance")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("resourceWorkspace")
        self.items: tuple[str, ...] = ()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else 2

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return str(index.row() + 1) if index.column() == 0 else self.items[index.row()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 0:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 1:
            return self.items[index.row()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def replace(self, items: tuple[str, ...]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class TranscriptView(QTableView):
    def count(self) -> int:
        """Compatibility helper matching the previous list widget API."""
        return self.model().rowCount()


class SummaryCard(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(14, 12, 14, 12)
        self.content_layout.setSpacing(6)
        title_label = QLabel(title.upper())
        title_label.setProperty("cardTitle", True)
        self.primary = QLabel()
        self.primary.setWordWrap(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.details = QLabel()
        self.details.setProperty("muted", True)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (title_label, self.primary, self.status, self.details):
            self.content_layout.addWidget(widget)

    def show_empty(self, message: str) -> None:
        self.primary.setText(message)
        self.primary.setProperty("primaryValue", False)
        self.status.clear()
        self.details.clear()
        self._repolish(self.primary)

    def show_content(self, primary: str, status: str, details: str, *, success=False) -> None:
        self.primary.setText(primary)
        self.primary.setProperty("primaryValue", True)
        self.status.setText(status)
        self.status.setProperty("status", "success" if success else "")
        self.details.setText(details)
        self._repolish(self.primary)
        self._repolish(self.status)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class ResourceWorkspace(QWidget):
    def __init__(self, explorer_service: ProjectExplorerService,
                 review_service: AlignmentReviewService,
                 pedagogical_reviews: PedagogicalReviewService, parent=None) -> None:
        super().__init__(parent)
        self._service = explorer_service
        self._resource: ResourceIdentity | None = None
        self.title = QLabel(objectName="resourceTitle")
        self.breadcrumb = QLabel(objectName="resourceBreadcrumb")
        self.breadcrumb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header = QWidget(objectName="resourceHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 14, 18, 8)
        header_layout.setSpacing(2)
        header_layout.addWidget(self.title)
        header_layout.addWidget(self.breadcrumb)

        self.tabs = QTabWidget(objectName="resourceTabs")
        # The native document-mode bar is centered on macOS; resource navigation
        # is intentionally anchored to the workspace content edge instead.
        self.tabs.setDocumentMode(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setDrawBase(False)
        self.overview_tab = QWidget(objectName="overviewTab")
        self.transcript_tab = QWidget(objectName="transcriptTab")
        self.alignment_tab = QWidget(objectName="alignmentTab")
        self.review_tab = AlignmentReviewWidget(
            review_service, review_service=pedagogical_reviews, external_context=True,
        )
        self.review_tab.approvalCompleted.connect(self._refresh_overview)
        self.review_tab.setObjectName("reviewTab")
        self.assets_tab = QWidget(objectName="assetsTab")
        for label, widget in (("Overview", self.overview_tab),
                              ("Transcript", self.transcript_tab),
                              ("Alignment", self.alignment_tab),
                              ("Review", self.review_tab), ("Assets", self.assets_tab)):
            self.tabs.addTab(widget, label)
        self._build_overview()
        self._build_transcript()
        self._build_alignment()
        self._build_assets()

        resource_page = QWidget(objectName="resourcePage")
        resource_layout = QVBoxLayout(resource_page)
        resource_layout.setContentsMargins(0, 0, 0, 0)
        resource_layout.setSpacing(0)
        resource_layout.addWidget(header)
        resource_layout.addWidget(self.tabs, 1)
        self.empty_state = QLabel(objectName="workspaceEmptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setWordWrap(True)
        self.empty_state.setProperty("muted", True)
        self.stack = QStackedLayout(self)
        self.stack.addWidget(self.empty_state)
        self.stack.addWidget(resource_page)
        self.clear()

    def _build_overview(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(14)
        cards = QGridLayout()
        cards.setSpacing(12)
        self.audio_card = SummaryCard("Audio")
        self.transcript_card = SummaryCard("Transcript")
        self.alignment_card = SummaryCard("Alignment")
        self.review_card = SummaryCard("Review")
        self.audio_value = self.audio_card.details
        self.transcript_value = self.transcript_card.details
        self.alignment_value = self.alignment_card.details
        self.review_value = self.review_card.primary
        self.review_progress = QProgressBar(objectName="overviewReviewProgress")
        self.review_progress.setTextVisible(False)
        self.continue_review_button = QPushButton("Continue Review", objectName="continueReviewButton")
        self.continue_review_button.setProperty("primaryAction", True)
        self.continue_review_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.review_tab))
        self.review_card.content_layout.addWidget(self.review_progress)
        self.review_card.content_layout.addWidget(self.continue_review_button, 0, Qt.AlignmentFlag.AlignLeft)
        for index, card in enumerate((self.audio_card, self.transcript_card,
                                      self.alignment_card, self.review_card)):
            cards.addWidget(card, index // 2, index % 2)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        layout.addLayout(cards)
        heading = QLabel("PROCESSING HISTORY")
        heading.setProperty("sectionHeading", True)
        layout.addWidget(heading)
        self.processing_table = self._table(
            ("Process", "Tool", "Status", "Completed"), "processingHistoryTable"
        )
        self.processing_table.setMinimumHeight(150)
        layout.addWidget(self.processing_table)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        QVBoxLayout(self.overview_tab).addWidget(scroll)

    def _build_transcript(self) -> None:
        heading_row = QHBoxLayout()
        heading = QLabel("Transcript")
        heading.setProperty("primaryValue", True)
        self.transcript_summary = QLabel(objectName="transcriptSummary")
        self.transcript_summary.setProperty("status", "success")
        heading_row.addWidget(heading)
        heading_row.addStretch()
        heading_row.addWidget(self.transcript_summary)
        self.transcript_model = TranscriptTableModel(self)
        self.transcript_list = TranscriptView(objectName="resourceTranscriptList")
        self.transcript_list.setModel(self.transcript_model)
        self.transcript_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.transcript_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.transcript_list.setAlternatingRowColors(True)
        self.transcript_list.verticalHeader().hide()
        self.transcript_list.verticalHeader().setDefaultSectionSize(38)
        self.transcript_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.transcript_list.setColumnWidth(0, 52)
        self.transcript_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.transcript_empty = QLabel(objectName="transcriptEmptyState")
        self.transcript_empty.setProperty("muted", True)
        layout = QVBoxLayout(self.transcript_tab)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.addLayout(heading_row)
        layout.addWidget(self.transcript_empty)
        layout.addWidget(self.transcript_list, 1)

    def _build_alignment(self) -> None:
        self.alignment_empty = QLabel(objectName="alignmentEmptyState")
        self.alignment_empty.setProperty("muted", True)
        self.alignment_table = self._table(
            ("Alignment", "Tier", "Items", "Tool", "Status", "Completed"),
            "resourceAlignmentTable",
        )
        self._configure_columns(self.alignment_table, compact=(2,), stretch=(0, 3))
        layout = QVBoxLayout(self.alignment_tab)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.addWidget(self.alignment_empty)
        layout.addWidget(self.alignment_table, 1)

    def _build_assets(self) -> None:
        self.assets_empty = QLabel(objectName="assetsEmptyState")
        self.assets_empty.setProperty("muted", True)
        self.assets_table = self._table(
            ("File", "Role", "Kind / MIME", "Size", "Location"), "resourceAssetsTable",
        )
        self._configure_columns(self.assets_table, compact=(3,), stretch=(0, 4))
        layout = QVBoxLayout(self.assets_tab)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.addWidget(self.assets_empty)
        layout.addWidget(self.assets_table, 1)

    @staticmethod
    def _table(headers: tuple[str, ...], name: str) -> QTableWidget:
        table = QTableWidget(0, len(headers), objectName=name)
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(30)
        return table

    @staticmethod
    def _configure_columns(table: QTableWidget, *, compact: tuple[int, ...],
                           stretch: tuple[int, ...]) -> None:
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(table.columnCount()):
            mode = QHeaderView.ResizeMode.Stretch if column in stretch else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)
        for column in compact:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def set_resource(self, resource: ResourceIdentity | None) -> None:
        self.review_tab.set_context(resource.source_id if resource else None,
                                    resource.source_unit_id if resource else None)
        self._resource = resource
        if resource is None:
            self._clear_data()
            self.empty_state.setText("Select a resource in the Corpus Explorer.")
            self.breadcrumb.setText("Select a resource in the Corpus Explorer.")
            self.stack.setCurrentIndex(0)
            return
        self._show_overview(self._service.load_overview(resource))
        self.stack.setCurrentIndex(1)
        self.tabs.setCurrentWidget(self.overview_tab)

    def clear(self) -> None:
        self._resource = None
        self.review_tab.set_context(None, None)
        self._clear_data()
        self.empty_state.setText("Open a project to browse corpus resources.")
        self.breadcrumb.setText("Open a project to browse corpus resources.")
        self.stack.setCurrentIndex(0)

    def show_empty_project(self) -> None:
        self._resource = None
        self.review_tab.set_context(None, None)
        self._clear_data()
        self.empty_state.setText(
            "No resources yet.\nAdd your first audio, transcript, or text resource to begin."
        )
        self.breadcrumb.setText("No resources yet.")
        self.stack.setCurrentIndex(0)

    def shutdown(self) -> None:
        self.review_tab.shutdown()

    def _refresh_overview(self) -> None:
        if self._resource is not None:
            self._show_overview(self._service.load_overview(self._resource))

    def _clear_data(self) -> None:
        self.title.clear()
        for card, message in ((self.audio_card, "No audio available"),
                              (self.transcript_card, "No authoritative transcript"),
                              (self.alignment_card, "No alignment evidence"),
                              (self.review_card, "No reviewable utterances")):
            card.show_empty(message)
        self.review_progress.setValue(0)
        self.review_progress.hide()
        self.continue_review_button.setEnabled(False)
        self.transcript_model.replace(())
        self.transcript_summary.clear()
        self.transcript_empty.setText("No authoritative transcript")
        self.alignment_empty.setText("No alignment evidence")
        self.assets_empty.setText("No assets available")
        for table in (self.processing_table, self.alignment_table, self.assets_table):
            table.setRowCount(0)
        self.tabs.setCurrentWidget(self.overview_tab)

    def _show_overview(self, overview: ResourceOverview) -> None:
        self.title.setText(overview.label)
        self.breadcrumb.setText("  ›  ".join(overview.breadcrumb))
        audio = [asset for asset in overview.assets if asset.asset_kind == "audio"]
        if audio:
            asset = audio[0]
            extra = f"\n+ {len(audio) - 1} additional audio asset(s)" if len(audio) > 1 else ""
            self.audio_card.show_content(
                asset.label, "Available" if asset.local_available else "Location unavailable",
                f"{asset.mime_type or asset.asset_kind or 'Unknown type'} · {_size(asset.byte_size)}{extra}",
                success=asset.local_available,
            )
        else:
            self.audio_card.show_empty("No audio available")
        total = len(overview.utterances)
        if overview.representation_kinds or total:
            kinds = ", ".join(overview.representation_kinds) or "Canonical text"
            self.transcript_card.show_content(
                "Authoritative", f"{total} utterance{'s' if total != 1 else ''}",
                f"Representation: {kinds}\nSource: {overview.transcript_source_label or 'Recorded provenance'}",
                success=True,
            )
        else:
            self.transcript_card.show_empty("No authoritative transcript")
        if overview.alignments:
            item = overview.alignments[0]
            counts = ", ".join(f"{entry.item_count} {entry.tier or 'items'}" for entry in overview.alignments)
            self.alignment_card.show_content(
                item.name, item.status.replace("_", " ").title(),
                f"{counts}\nTool: {item.tool_name or 'Unknown'}\nLatest: {item.completed_at or '—'}",
                success=item.status.lower() in {"succeeded", "completed", "available"},
            )
        else:
            self.alignment_card.show_empty("No alignment evidence")
        if total:
            self.review_card.show_content(
                f"{overview.approved_count} of {total} utterances approved",
                "Approved" if overview.approved_count == total else "Needs review", "",
                success=overview.approved_count == total,
            )
            self.review_progress.setMaximum(total)
            self.review_progress.setValue(overview.approved_count)
            self.review_progress.show()
        else:
            self.review_card.show_empty("No reviewable utterances")
            self.review_progress.hide()
        self.continue_review_button.setEnabled(bool(total))
        self.transcript_model.replace(overview.utterances)
        self.transcript_summary.setText(
            f"Authoritative · {total} utterance{'s' if total != 1 else ''}" if total else ""
        )
        self.transcript_empty.setText("" if total else "No authoritative transcript")
        self._fill_table(self.alignment_table, (
            (item.name, item.tier or "—", str(item.item_count), item.tool_name or "—",
             item.status, item.completed_at or "—") for item in overview.alignments
        ))
        self.alignment_empty.setText("" if overview.alignments else "No alignment evidence")
        self._fill_table(self.assets_table, (
            (asset.label, asset.role or "—", asset.mime_type or asset.asset_kind or "—",
             _size(asset.byte_size), str(asset.local_path) if asset.local_available else "Unavailable")
            for asset in overview.assets
        ), tooltip_columns=(0, 4))
        self.assets_empty.setText("" if overview.assets else "No assets available")
        self._fill_table(self.processing_table, (
            (item.process_type, item.tool_name or "—", item.status, item.completed_at or "—")
            for item in overview.processing_history
        ))
        self._configure_columns(self.processing_table, compact=(2, 3), stretch=(0, 1))

    @staticmethod
    def _fill_table(table: QTableWidget, rows, *, tooltip_columns=()) -> None:
        values = tuple(rows)
        table.setRowCount(len(values))
        for row, columns in enumerate(values):
            for column, value in enumerate(columns):
                item = QTableWidgetItem(value)
                if column in tooltip_columns:
                    item.setToolTip(value)
                table.setItem(row, column, item)


def _size(byte_size: int) -> str:
    if byte_size < 1024:
        return f"{byte_size} B"
    if byte_size < 1024 * 1024:
        return f"{byte_size / 1024:.1f} KB"
    return f"{byte_size / (1024 * 1024):.1f} MB"
