from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QSplitter

from lexbundler.application.project_service import ProjectService
from lexbundler.application.alignment_review_service import (
    ReviewAlignment, ReviewApproval, ReviewSelection, ReviewSource,
    ReviewUtterance, ReviewWord,
)
from lexbundler.application.waveform import WaveformBucket, WaveformWindow
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.alignment_review_widget import AlignmentReviewWidget


class FakePlayback(QObject):
    errorOccurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.stop_calls = 0
        self.play_calls = []
        self.duration_ms = 5000

    def stop(self):
        self.stop_calls += 1

    def play(self, path, interval):
        self.play_calls.append((path, interval))

    def shutdown(self):
        self.stop()


class FakeWaveformLoader(QObject):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.cancel_calls = 0

    def request(self, **request):
        self.requests.append(request)

    def cancel(self):
        self.cancel_calls += 1


def _project(tmp_path: Path):
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(tmp_path / "ui.lexbundler", name="UI")
    source = service.corpus.create_source("Source")
    unit = service.corpus.create_source_unit(source.id, kind="part", label="Part")
    transcript = tmp_path / "ui.txt"
    transcript.write_text("你好\n再见", encoding="utf-8")
    service.transcript_imports.import_utf8(
        transcript, source_id=source.id, source_unit_id=unit.id
    )
    return service


def test_widget_populates_and_clears_without_alignment(
    qapplication: QApplication, tmp_path: Path
) -> None:
    service = _project(tmp_path)
    playback = FakePlayback()
    widget = AlignmentReviewWidget(service.alignment_review, playback)

    widget.refresh()
    assert isinstance(widget.review_splitter, QSplitter)
    assert widget.review_splitter.count() == 2
    assert [button.text() for button in widget._start_nudges[1]] == [
        "-50", "-10", "+10", "+50"
    ]
    assert all(button.maximumWidth() == 52 for button in widget._start_nudges[1])
    assert widget.reset_button.text() == "Reset Proposed"
    assert widget.source_combo.currentText() == "Source"
    assert widget.unit_combo.count() == 2
    widget.unit_combo.setCurrentIndex(1)
    assert widget.transcript_model.rowCount() == 2
    assert widget.text_label.text() == "你好"
    assert widget.word_model.rowCount() == 0
    assert "No MFA" in widget.message_label.text()
    assert not widget.speech_button.isEnabled()

    before = playback.stop_calls
    widget.transcript_list.setCurrentIndex(widget.transcript_model.index(1, 0))
    assert widget.text_label.text() == "再见"
    assert playback.stop_calls > before

    widget.clear()
    assert widget.transcript_model.rowCount() == 0
    assert not widget.source_combo.isEnabled()
    widget.close()


def test_widget_shows_words_enables_playback_and_requests_stop_on_selection(
    qapplication: QApplication, tmp_path: Path
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    words = (ReviewWord("你好", 0, 0, 2, 100, 400),)
    first = ReviewUtterance(
        1, 0, "你好", 0, 2, 1, "Source", None, None, 8, 3, audio,
        100, 400, 0, 500, words, True, None,
    )
    second = ReviewUtterance(
        2, 1, "再见", 3, 5, 1, "Source", None, None, 8, 3, audio,
        600, 900, 500, 1000, (), True, None,
    )

    class Projection:
        def list_sources(self):
            return (ReviewSource(1, "Source"),)

        def list_units(self, _source_id):
            return ()

        def load(self, _source_id, _unit_id, *, alignment_layer_id=None):
            return ReviewSelection((ReviewAlignment(8, "MFA"),), 8, (first, second))

    playback = FakePlayback()
    loader = FakeWaveformLoader()
    widget = AlignmentReviewWidget(Projection(), playback, loader)
    widget.refresh()

    assert widget.word_model.rowCount() == 1
    assert widget.speech_button.isEnabled()
    assert widget.timing_label.text() == "MFA speech: 0.100 – 0.400 s"
    assert widget.proposed_timing_label.text() == "Proposed clip: 0.050 – 0.500 s"
    assert loader.requests[-1]["start_ms"] == 0
    assert loader.requests[-1]["end_ms"] == 1400
    loader.loaded.emit(WaveformWindow(
        3, audio, 0, 1400, (WaveformBucket(-0.5, 0.5),)
    ))
    widget._start_nudges[1][-1].click()
    widget.study_button.click()
    assert (playback.play_calls[-1][1].start_ms,
            playback.play_calls[-1][1].end_ms) == (100, 500)
    widget.reset_button.click()
    assert widget.proposed_timing_label.text() == "Proposed clip: 0.050 – 0.500 s"
    stopped = playback.stop_calls
    widget.transcript_list.setCurrentIndex(widget.transcript_model.index(1, 0))
    assert playback.stop_calls > stopped
    assert widget.text_label.text() == "再见"
    assert widget.proposed_timing_label.text() == "Proposed clip: 0.550 – 1.000 s"
    widget.close()


def test_approved_baseline_dirty_reset_and_explicit_approval(
    qapplication: QApplication, tmp_path: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    approval = ReviewApproval(
        20, 21, 22, 23, 24, 80, 480
    )
    item = ReviewUtterance(
        1, 0, "你好", 0, 2, 1, "Source", None, None, 8, 3, audio,
        100, 400, 0, 500, (), True, None, 9, 10, approval,
    )

    class Projection:
        current = item

        def list_sources(self):
            return (ReviewSource(1, "Source"),)

        def list_units(self, _source_id):
            return ()

        def load(self, _source_id, _unit_id, *, alignment_layer_id=None):
            return ReviewSelection((ReviewAlignment(8, "MFA"),), 8, (self.current,))

    class ReviewService:
        requests = []

        def approve(self, request):
            self.requests.append(request)
            updated = ReviewApproval(
                30, 31, 32, 33, 34, request.approved_start_ms,
                request.approved_end_ms,
            )
            projection.current = replace(projection.current, approval=updated)
            return object()

    projection = Projection()
    reviews = ReviewService()
    playback = FakePlayback()
    loader = FakeWaveformLoader()
    widget = AlignmentReviewWidget(
        projection, playback, loader, reviews
    )
    widget.refresh()
    loader.loaded.emit(WaveformWindow(
        3, audio, 0, 1400, (WaveformBucket(-0.5, 0.5),)
    ))

    assert widget.proposed_timing_label.text() == "Proposed clip: 0.080 – 0.480 s"
    assert widget.approved_timing_label.text() == "Approved clip: 0.080 – 0.480 s"
    assert widget.approve_button.text() == "Re-approve"
    widget._start_nudges[1][-1].click()
    assert "modified — approval required" in widget.proposed_timing_label.text()
    widget.reset_button.click()
    assert "modified" not in widget.proposed_timing_label.text()
    widget._end_nudges[1][0].click()
    widget.approve_button.click()

    assert len(reviews.requests) == 1
    assert reviews.requests[0].approved_end_ms == 430
    assert reviews.requests[0].manually_edited is True
    assert projection.current.approval.processing_run_id == 34
    assert widget.message_label.text() == "Selection approved."
    widget.close()


def test_explorer_context_hides_old_selectors_and_cancels_stale_activity(
    qapplication: QApplication,
) -> None:
    class Projection:
        calls = []

        def load(self, source_id, unit_id, *, alignment_layer_id=None):
            self.calls.append((source_id, unit_id, alignment_layer_id))
            return ReviewSelection((), None, ())

    projection = Projection()
    playback = FakePlayback()
    loader = FakeWaveformLoader()
    widget = AlignmentReviewWidget(
        projection, playback, loader, external_context=True
    )

    widget.set_context(1, 10)
    stops = playback.stop_calls
    cancels = loader.cancel_calls
    widget.set_context(2, 20)

    assert not widget.context_controls.isVisible()
    assert projection.calls == [(1, 10, None), (2, 20, None)]
    assert playback.stop_calls > stops
    assert loader.cancel_calls > cancels
    assert widget.transcript_model.rowCount() == 0
    widget.close()
