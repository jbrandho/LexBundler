from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import QApplication

from lexbundler.application.alignment_review_service import ReviewUtterance
from lexbundler.ui.playback import (
    PlaybackController, PlaybackInterval, context_interval, speech_interval,
    study_interval,
)


def _item(**changes) -> ReviewUtterance:
    base = ReviewUtterance(
        1, 0, "文本", 0, 2, 1, "S", None, None, 3, 4, Path("audio.wav"),
        40, 1000, 20, 1080, (), True, None,
    )
    return replace(base, **changes)


def test_playback_interval_calculations() -> None:
    item = _item()
    assert speech_interval(item) == PlaybackInterval(40, 1000)
    assert study_interval(item).start_ms == 20  # zero clamp, then known silence
    assert study_interval(item).end_ms == 1080  # does not cross known next speech
    assert context_interval(item) == PlaybackInterval(0, 2000)
    assert context_interval(item, duration_ms=1500).end_ms == 1500


def test_intervals_handle_missing_alignment_and_zero_clamp() -> None:
    assert speech_interval(_item(speech_start_ms=None, speech_end_ms=None)) is None
    interval = study_interval(
        _item(speech_start_ms=20, preceding_silence_start_ms=None,
              following_silence_end_ms=None)
    )
    assert (interval.start_ms, interval.end_ms) == (0, 1150)


def test_controller_stops_when_position_reaches_target(
    qapplication: QApplication,
) -> None:
    controller = PlaybackController()
    controller._stop_at_ms = 500
    controller._position_changed(499)
    assert controller._stop_at_ms == 500
    controller._position_changed(500)
    assert controller._stop_at_ms is None
