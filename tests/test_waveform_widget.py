from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lexbundler.application.waveform import WaveformBucket, WaveformWindow
from lexbundler.ui.waveform_widget import WaveformWidget


def _widget() -> WaveformWidget:
    widget = WaveformWidget()
    widget.resize(600, 180)
    widget.set_context(
        start_ms=4000, end_ms=12000, speech_start_ms=5000,
        speech_end_ms=11000, proposed_start_ms=4950, proposed_end_ms=11150,
    )
    return widget


def test_time_mapping_tracks_resize_and_no_data_renders(
    qapplication: QApplication,
) -> None:
    widget = _widget()
    original_x = widget.time_to_x(8000)
    assert widget.x_to_time(original_x) == 8000
    widget.resize(900, 180)
    assert widget.time_to_x(8000) > original_x
    assert widget.x_to_time(widget.time_to_x(8000)) == 8000
    widget.set_unavailable("No waveform")
    widget.show()
    qapplication.processEvents()
    widget.close()


def test_waveform_projection_and_drag_clamp_leave_mfa_unchanged(
    qapplication: QApplication, tmp_path,
) -> None:
    widget = _widget()
    widget.set_waveform(WaveformWindow(
        1, tmp_path / "audio.wav", 4000, 12000,
        (WaveformBucket(-0.5, 0.7), WaveformBucket(-1, 1)),
    ))
    widget.show()
    qapplication.processEvents()
    moved = []
    widget.boundaryMoved.connect(lambda boundary, value: moved.append((boundary, value)))
    start = QPoint(round(widget.time_to_x(4950)), 80)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(widget, QPoint(widget.width() + 100, 80))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(widget.width() + 100, 80))

    assert moved[-1] == ("start", 11149)
    assert widget.speech_bounds == (5000, 11000)
    widget.set_provisional_bounds(11149, 11150)
    assert widget.proposed_bounds == (11149, 11150)
    widget.close()
