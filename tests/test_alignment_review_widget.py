from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from lexbundler.application.project_service import ProjectService
from lexbundler.application.alignment_review_service import (
    ReviewAlignment, ReviewSelection, ReviewSource, ReviewUtterance, ReviewWord,
)
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
    widget = AlignmentReviewWidget(Projection(), playback)
    widget.refresh()

    assert widget.word_model.rowCount() == 1
    assert widget.speech_button.isEnabled()
    widget.study_button.click()
    assert playback.play_calls[-1][1].start_ms == 50
    stopped = playback.stop_calls
    widget.transcript_list.setCurrentIndex(widget.transcript_model.index(1, 0))
    assert playback.stop_calls > stopped
    assert widget.text_label.text() == "再见"
    widget.close()
