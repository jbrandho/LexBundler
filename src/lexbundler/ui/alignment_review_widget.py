"""Native Qt alignment and pedagogical review workspace."""

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QListView,
    QPushButton, QSizePolicy, QSplitter, QTableView, QVBoxLayout, QWidget,
)

from lexbundler.application.alignment_review_service import (
    AlignmentReviewService, ReviewUtterance, ReviewWord,
)
from lexbundler.application.provisional_boundaries import ProvisionalBoundaryModel
from lexbundler.application.pedagogical_review_service import (
    PedagogicalReviewRequest, PedagogicalReviewService,
)
from lexbundler.application.waveform import WaveformWindow
from lexbundler.ui.playback import (
    PlaybackController, PlaybackInterval, context_interval, speech_interval,
)
from lexbundler.ui.waveform_loader import WaveformLoader
from lexbundler.ui.waveform_widget import WaveformWidget


def format_seconds(milliseconds: int | None) -> str:
    return "—" if milliseconds is None else f"{milliseconds / 1000:.3f}"


class TranscriptListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: tuple[ReviewUtterance, ...] = ()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return f"{index.row() + 1}    {self.items[index.row()].text}"
        if index.isValid() and role == Qt.ItemDataRole.ToolTipRole:
            return self.items[index.row()].text
        return None

    def replace(self, items: tuple[ReviewUtterance, ...]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class WordAlignmentModel(QAbstractTableModel):
    HEADERS = ("MFA word", "Start", "End", "Duration")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.items: tuple[ReviewWord, ...] = ()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        word = self.items[index.row()]
        return (
            word.label, format_seconds(word.start_ms), format_seconds(word.end_ms),
            format_seconds(word.end_ms - word.start_ms),
        )[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def replace(self, items: tuple[ReviewWord, ...]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class AlignmentReviewWidget(QWidget):
    """Browse authoritative turns and inspect selected MFA timing evidence."""

    approvalCompleted = Signal()

    def __init__(
        self, service: AlignmentReviewService,
        playback: PlaybackController | None = None,
        waveform_loader: WaveformLoader | None = None,
        review_service: PedagogicalReviewService | None = None,
        external_context: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._playback = playback or PlaybackController(self)
        self._waveform_loader = waveform_loader or WaveformLoader(self)
        self._review_service = review_service
        self._external_context = external_context
        self._selected_source_id: int | None = None
        self._selected_source_unit_id: int | None = None
        self._current: ReviewUtterance | None = None
        self._boundaries: ProvisionalBoundaryModel | None = None

        self.source_combo = QComboBox(objectName="reviewSourceCombo")
        self.unit_combo = QComboBox(objectName="reviewUnitCombo")
        self.alignment_combo = QComboBox(objectName="reviewAlignmentCombo")
        self.context_controls = QWidget(objectName="reviewContextControls")
        controls = QFormLayout(self.context_controls)
        controls.addRow("Source:", self.source_combo)
        controls.addRow("Unit:", self.unit_combo)
        self.context_controls.setVisible(not external_context)
        alignment_row = QFormLayout()
        alignment_row.addRow("Alignment evidence:", self.alignment_combo)

        self.transcript_model = TranscriptListModel(self)
        self.transcript_list = QListView(objectName="transcriptTurnList")
        self.transcript_list.setModel(self.transcript_model)
        self.transcript_list.setAlternatingRowColors(True)
        self.transcript_list.setSpacing(2)

        self.text_label = QLabel("Open a project to review alignment.", objectName="reviewText")
        self.text_label.setWordWrap(True)
        text_font = QFont(self.text_label.font())
        text_font.setPointSize(max(20, text_font.pointSize() + 8))
        self.text_label.setFont(text_font)
        self.timing_label = QLabel(objectName="reviewTiming")
        self.proposed_timing_label = QLabel(objectName="proposedTiming")
        self.approved_timing_label = QLabel(objectName="approvedTiming")
        self.message_label = QLabel(objectName="reviewMessage")
        self.message_label.setWordWrap(True)
        self.waveform = WaveformWidget()
        self.waveform.setObjectName("reviewWaveform")
        self.word_model = WordAlignmentModel(self)
        self.word_table = QTableView(objectName="alignmentWordTable")
        self.word_table.setModel(self.word_model)
        self.word_table.setAlternatingRowColors(True)
        self.word_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.word_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self.word_table.verticalHeader().hide()
        self.word_table.verticalHeader().setDefaultSectionSize(28)

        self.speech_button = QPushButton("Play MFA Speech", objectName="playSpeechButton")
        self.study_button = QPushButton("Play Proposed Clip", objectName="playStudyButton")
        self.context_button = QPushButton("Play Context", objectName="playContextButton")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for button in (self.speech_button, self.study_button, self.context_button):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            buttons.addWidget(button)
        buttons.addStretch()

        nudge_layout = QVBoxLayout()
        self._start_nudges = self._nudge_row("start")
        self._end_nudges = self._nudge_row("end")
        start_label = QLabel("Start")
        start_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        end_label = QLabel("End")
        end_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nudge_layout.addWidget(start_label)
        nudge_layout.addWidget(self._start_nudges[0])
        nudge_layout.addWidget(end_label)
        nudge_layout.addWidget(self._end_nudges[0])
        self.reset_button = QPushButton("Reset Proposed", objectName="resetBoundsButton")
        self.approve_button = QPushButton("Approve Selection", objectName="approveSelectionButton")
        self.approve_button.setProperty("successAction", True)
        self.approve_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row = QHBoxLayout()
        action_row.addLayout(buttons)
        action_row.addStretch()
        action_row.addWidget(self.approve_button)

        detail = QFrame(objectName="reviewDetailFrame")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(8)
        detail_layout.addWidget(self.text_label)
        detail_layout.addWidget(self.timing_label)
        detail_layout.addWidget(self.proposed_timing_label)
        detail_layout.addWidget(self.approved_timing_label)
        detail_layout.addWidget(self.waveform)
        compact_controls = QHBoxLayout()
        compact_controls.addLayout(nudge_layout)
        compact_controls.addStretch()
        detail_layout.addLayout(compact_controls)
        detail_layout.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignLeft)
        detail_layout.addLayout(action_row)
        detail_layout.addWidget(self.message_label)
        word_heading = QLabel("MFA ACOUSTIC ALIGNMENT WORDS")
        word_heading.setProperty("sectionHeading", True)
        detail_layout.addWidget(word_heading)
        detail_layout.addWidget(self.word_table, 1)
        list_frame = QFrame(objectName="reviewListFrame")
        list_layout = QVBoxLayout(list_frame)
        list_layout.setContentsMargins(0, 12, 0, 0)
        list_heading = QLabel("UTTERANCES", objectName="reviewUtterancesHeading")
        list_heading.setProperty("sectionHeading", True)
        list_heading.setContentsMargins(12, 0, 12, 4)
        list_layout.addWidget(list_heading)
        list_layout.addWidget(self.transcript_list, 1)
        self.review_splitter = QSplitter(objectName="reviewInnerSplitter")
        self.review_splitter.addWidget(list_frame)
        self.review_splitter.addWidget(detail)
        self.review_splitter.setChildrenCollapsible(False)
        self.review_splitter.setStretchFactor(0, 0)
        self.review_splitter.setStretchFactor(1, 1)
        self.review_splitter.setSizes([280, 760])

        layout = QVBoxLayout(self)
        layout.addWidget(self.context_controls)
        layout.addLayout(alignment_row)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.review_splitter, 1)

        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.unit_combo.currentIndexChanged.connect(self._unit_changed)
        self.alignment_combo.currentIndexChanged.connect(self._alignment_changed)
        self.transcript_list.selectionModel().currentChanged.connect(self._turn_changed)
        self.speech_button.clicked.connect(lambda: self._play(speech_interval))
        self.study_button.clicked.connect(self._play_proposed)
        self.context_button.clicked.connect(
            lambda: self._play(lambda item: context_interval(item, duration_ms=self._playback.duration_ms))
        )
        self._playback.errorOccurred.connect(self.message_label.setText)
        self._waveform_loader.loaded.connect(self._waveform_loaded)
        self._waveform_loader.failed.connect(self._waveform_failed)
        self.waveform.boundaryMoved.connect(self._waveform_boundary_moved)
        self.reset_button.clicked.connect(self._reset_boundaries)
        self.approve_button.clicked.connect(self._approve_selection)
        self.clear()

    def _nudge_row(self, boundary: str) -> tuple[QWidget, tuple[QPushButton, ...]]:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        buttons: list[QPushButton] = []
        for delta in (-50, -10, 10, 50):
            sign = "+" if delta > 0 else ""
            button = QPushButton(f"{sign}{delta}")
            button.setProperty("compact", True)
            button.setFixedWidth(52)
            button.setObjectName(f"nudge{boundary.title()}{sign.replace('+', 'Plus')}{delta if delta > 0 else 'Minus' + str(abs(delta))}")
            button.clicked.connect(
                lambda _checked=False, name=boundary, amount=delta: self._nudge(name, amount)
            )
            layout.addWidget(button)
            buttons.append(button)
        return container, tuple(buttons)

    def refresh(self) -> None:
        if self._external_context:
            if self._selected_source_id is not None:
                self._load(None)
            return
        previous_source = self.source_combo.currentData()
        self._playback.stop()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for source in self._service.list_sources():
            self.source_combo.addItem(source.label, source.id)
        if previous_source is not None:
            index = self.source_combo.findData(previous_source)
            self.source_combo.setCurrentIndex(max(0, index))
        self.source_combo.blockSignals(False)
        self.source_combo.setEnabled(self.source_combo.count() > 0)
        if self.source_combo.count():
            self._source_changed()
        else:
            self._set_empty("This project has no corpus sources.")

    def clear(self) -> None:
        self._playback.stop()
        self._waveform_loader.cancel()
        self._selected_source_id = None
        self._selected_source_unit_id = None
        for combo in (self.source_combo, self.unit_combo, self.alignment_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.setEnabled(False)
            combo.blockSignals(False)
        self.transcript_model.replace(())
        self._show_item(None, "Open a project to review alignment.")

    def set_context(self, source_id: int | None, source_unit_id: int | None) -> None:
        """Load one Explorer-selected source scope and discard stale review state."""
        self._playback.stop()
        self._waveform_loader.cancel()
        self._selected_source_id = source_id
        self._selected_source_unit_id = source_unit_id
        self.alignment_combo.blockSignals(True)
        self.alignment_combo.clear()
        self.alignment_combo.blockSignals(False)
        if source_id is None:
            self._set_empty("Select a resource in the Corpus Explorer.")
        else:
            self._load(None)

    def shutdown(self) -> None:
        """Release native playback relationships before widget destruction."""
        self._waveform_loader.cancel()
        self._playback.shutdown()

    def _source_changed(self) -> None:
        source_id = self.source_combo.currentData()
        self._selected_source_id = source_id
        self._playback.stop()
        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        self.unit_combo.addItem("Entire source", None)
        if source_id is not None:
            for unit in self._service.list_units(source_id):
                self.unit_combo.addItem(unit.label, unit.id)
        self.unit_combo.setEnabled(source_id is not None)
        self.unit_combo.blockSignals(False)
        self._unit_changed()

    def _unit_changed(self) -> None:
        self._selected_source_unit_id = self.unit_combo.currentData()
        self._load(None)

    def _alignment_changed(self) -> None:
        self._load(self.alignment_combo.currentData())

    def _load(self, alignment_id: int | None) -> None:
        self._playback.stop()
        self._waveform_loader.cancel()
        source_id = (
            self._selected_source_id if self._external_context
            else self.source_combo.currentData()
        )
        source_unit_id = (
            self._selected_source_unit_id if self._external_context
            else self.unit_combo.currentData()
        )
        if source_id is None:
            self._set_empty("Select a corpus source.")
            return
        result = self._service.load(
            source_id, source_unit_id, alignment_layer_id=alignment_id
        )
        self.alignment_combo.blockSignals(True)
        self.alignment_combo.clear()
        for alignment in result.alignments:
            self.alignment_combo.addItem(alignment.label, alignment.layer_id)
        if result.selected_alignment_layer_id is not None:
            self.alignment_combo.setCurrentIndex(
                self.alignment_combo.findData(result.selected_alignment_layer_id)
            )
        self.alignment_combo.setEnabled(self.alignment_combo.count() > 1)
        self.alignment_combo.blockSignals(False)
        self.transcript_model.replace(result.utterances)
        if result.utterances:
            self.transcript_list.setCurrentIndex(self.transcript_model.index(0, 0))
        else:
            self._show_item(None, "No reviewable utterances")

    def _set_empty(self, message: str) -> None:
        self.transcript_model.replace(())
        self._show_item(None, message)

    def _turn_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self._playback.stop()
        self._waveform_loader.cancel()
        item = self.transcript_model.items[current.row()] if current.isValid() else None
        self._show_item(item)

    def _show_item(self, item: ReviewUtterance | None, message: str | None = None) -> None:
        self._current = item
        self._boundaries = None
        self.text_label.setText(item.text if item else (message or "Select a transcript turn."))
        self.timing_label.setText(
            "" if item is None else
            f"MFA speech: {format_seconds(item.speech_start_ms)} – {format_seconds(item.speech_end_ms)} s"
        )
        self.proposed_timing_label.clear()
        self.approved_timing_label.setText(
            "Approved clip: Not approved" if item is not None else ""
        )
        self.word_model.replace(item.words if item else ())
        reason = item.playback_unavailable_reason if item else None
        self.message_label.setText(reason or "")
        available = bool(item and item.playback_available)
        for button in (self.speech_button, self.study_button, self.context_button):
            button.setEnabled(available)
        self.approve_button.setEnabled(False)
        if item is not None and item.approval is not None:
            approval = item.approval
            self.approved_timing_label.setText(
                f"Approved clip: {format_seconds(approval.start_ms)} – "
                f"{format_seconds(approval.end_ms)} s"
            )
            self.approve_button.setText("Re-approve")
        else:
            self.approve_button.setText("Approve Selection")
        self._set_editor_enabled(False)
        if (
            item is not None and item.playback_available
            and item.audio_asset_id is not None and item.audio_path is not None
            and item.speech_start_ms is not None and item.speech_end_ms is not None
        ):
            visible_start = max(0, item.speech_start_ms - 1000)
            visible_end = item.speech_end_ms + 1000
            self._boundaries = self._new_boundary_model(
                item, visible_start, visible_end
            )
            current = self._boundaries.current
            self.waveform.set_context(
                start_ms=visible_start, end_ms=visible_end,
                speech_start_ms=item.speech_start_ms,
                speech_end_ms=item.speech_end_ms,
                proposed_start_ms=current.start_ms, proposed_end_ms=current.end_ms,
            )
            self.waveform.set_loading()
            self._update_proposed_display()
            self._waveform_loader.request(
                asset_id=item.audio_asset_id, media_path=item.audio_path,
                start_ms=visible_start, end_ms=visible_end,
                bucket_count=max(300, self.waveform.width()),
            )
        else:
            self.waveform.clear()

    def _play(self, interval_factory) -> None:
        if self._current is None or self._current.audio_path is None:
            return
        interval = interval_factory(self._current)
        if interval is not None:
            self._playback.play(self._current.audio_path, interval)

    def _play_proposed(self) -> None:
        if self._current is None or self._current.audio_path is None or self._boundaries is None:
            return
        current = self._boundaries.current
        self._playback.play(
            self._current.audio_path, PlaybackInterval(current.start_ms, current.end_ms)
        )

    def _nudge(self, boundary: str, amount: int) -> None:
        if self._boundaries is None:
            return
        if boundary == "start":
            self._boundaries.nudge_start(amount)
        else:
            self._boundaries.nudge_end(amount)
        self._update_proposed_display()

    def _reset_boundaries(self) -> None:
        if self._boundaries is not None:
            self._boundaries.reset()
            self._update_proposed_display()

    def _waveform_boundary_moved(self, boundary: str, milliseconds: int) -> None:
        if self._boundaries is None:
            return
        if boundary == "start":
            self._boundaries.set_start(milliseconds)
        else:
            self._boundaries.set_end(milliseconds)
        self._update_proposed_display()

    def _update_proposed_display(self) -> None:
        if self._boundaries is None:
            self.proposed_timing_label.clear()
            return
        current = self._boundaries.current
        modified = bool(
            self._current is not None
            and self._current.approval is not None
            and (
                current.start_ms != self._current.approval.start_ms
                or current.end_ms != self._current.approval.end_ms
            )
        )
        self.proposed_timing_label.setText(
            f"Proposed clip: {format_seconds(current.start_ms)} – "
            f"{format_seconds(current.end_ms)} s"
            + (" (modified — approval required)" if modified else "")
        )
        self.waveform.set_provisional_bounds(current.start_ms, current.end_ms)

    def _waveform_loaded(self, waveform: WaveformWindow) -> None:
        if (
            self._current is not None
            and self._current.audio_asset_id == waveform.asset_id
            and self._current.audio_path == waveform.media_path
            and waveform.start_ms == max(0, self._current.speech_start_ms - 1000)
        ):
            if self._boundaries is not None and waveform.end_ms < self._boundaries.visible.end_ms:
                self._boundaries = self._new_boundary_model(
                    self._current, waveform.start_ms, waveform.end_ms
                )
                self._update_proposed_display()
            self.waveform.set_waveform(waveform)
            self._set_editor_enabled(True)
            self.approve_button.setEnabled(self._review_service is not None)

    def _waveform_failed(self, message: str) -> None:
        self.waveform.set_unavailable(f"Waveform unavailable: {message}")
        self._set_editor_enabled(False)
        self.approve_button.setEnabled(False)

    @staticmethod
    def _new_boundary_model(
        item: ReviewUtterance, visible_start: int, visible_end: int
    ) -> ProvisionalBoundaryModel:
        assert item.speech_start_ms is not None and item.speech_end_ms is not None
        return ProvisionalBoundaryModel(
            speech_start_ms=item.speech_start_ms,
            speech_end_ms=item.speech_end_ms,
            visible_start_ms=visible_start,
            visible_end_ms=visible_end,
            preceding_silence_start_ms=item.preceding_silence_start_ms,
            following_silence_end_ms=item.following_silence_end_ms,
            baseline_start_ms=item.approval.start_ms if item.approval else None,
            baseline_end_ms=item.approval.end_ms if item.approval else None,
        )

    def _set_editor_enabled(self, enabled: bool) -> None:
        for button in (*self._start_nudges[1], *self._end_nudges[1], self.reset_button):
            button.setEnabled(enabled)

    def _approve_selection(self) -> None:
        if (
            self._review_service is None or self._current is None
            or self._boundaries is None or self._current.text_span_id is None
            or self._current.audio_asset_id is None
        ):
            return
        current = self._boundaries.current
        request = PedagogicalReviewRequest(
            transcript_segment_id=self._current.segment_id,
            transcript_text_span_id=self._current.text_span_id,
            source_audio_asset_id=self._current.audio_asset_id,
            approved_start_ms=current.start_ms,
            approved_end_ms=current.end_ms,
            alignment_layer_id=self._current.alignment_layer_id,
            mfa_speech_start_ms=self._current.speech_start_ms,
            mfa_speech_end_ms=self._current.speech_end_ms,
            manually_edited=current != self._boundaries.default,
        )
        transcript_segment_id = self._current.segment_id
        try:
            self._review_service.approve(request)
        except Exception as error:
            self.message_label.setText(f"Approval failed: {error}")
            return
        self._reload_current(transcript_segment_id)
        self.message_label.setText("Selection approved.")
        self.approvalCompleted.emit()

    def _reload_current(self, transcript_segment_id: int) -> None:
        if self._current is None:
            return
        result = self._service.load(
            self._current.source_id, self._current.source_unit_id,
            alignment_layer_id=self._current.alignment_layer_id,
        )
        self.transcript_model.replace(result.utterances)
        row = next(
            (index for index, item in enumerate(result.utterances)
             if item.segment_id == transcript_segment_id),
            None,
        )
        if row is not None:
            self.transcript_list.setCurrentIndex(self.transcript_model.index(row, 0))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space and self._current is not None:
            self._play_proposed()
            event.accept()
            return
        super().keyPressEvent(event)
