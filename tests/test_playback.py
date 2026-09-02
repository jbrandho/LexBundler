from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
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


class FakePlayer(QObject):
    positionChanged = Signal(int)
    mediaStatusChanged = Signal(object)
    seekableChanged = Signal(bool)
    sourceChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self) -> None:
        super().__init__()
        self._source = QUrl()
        self._status = QMediaPlayer.MediaStatus.NoMedia
        self._seekable = False
        self._position = 0
        self.set_source_calls = []
        self.set_position_calls = []
        self.play_calls = 0
        self.stop_calls = 0
        self.accept_seek = True

    def source(self):
        return self._source

    def mediaStatus(self):
        return self._status

    def isSeekable(self):
        return self._seekable

    def position(self):
        return self._position

    def duration(self):
        return 10_000

    def setSource(self, source):
        self.set_source_calls.append(source)
        self._source = source
        self._status = QMediaPlayer.MediaStatus.LoadingMedia
        self._seekable = False
        self._position = 0
        self.sourceChanged.emit(source)
        self.mediaStatusChanged.emit(self._status)

    def setPosition(self, position):
        self.set_position_calls.append(position)
        if self.accept_seek:
            self._position = position
            self.positionChanged.emit(position)

    def play(self):
        self.play_calls += 1

    def stop(self):
        self.stop_calls += 1

    def finish_loading(self):
        self._status = QMediaPlayer.MediaStatus.LoadedMedia
        self.mediaStatusChanged.emit(self._status)
        self._seekable = True
        self.seekableChanged.emit(True)


def _controller(player: FakePlayer) -> PlaybackController:
    return PlaybackController(player=player)


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


def test_first_request_waits_for_loaded_seekable_media(tmp_path: Path) -> None:
    player = FakePlayer()
    controller = _controller(player)
    interval = PlaybackInterval(2000, 3000)

    controller.play(tmp_path / "audio.wav", interval)

    assert len(player.set_source_calls) == 1
    assert player.set_position_calls == []
    assert player.play_calls == 0
    assert controller._pending is not None
    assert controller._pending.interval == interval

    player.finish_loading()
    assert player.set_position_calls == [2000]
    assert player.play_calls == 1
    assert controller._stop_at_ms == 3000


def test_already_loaded_source_seeks_and_plays_immediately(tmp_path: Path) -> None:
    player = FakePlayer()
    path = (tmp_path / "audio.wav").resolve()
    player._source = QUrl.fromLocalFile(str(path))
    player._status = QMediaPlayer.MediaStatus.LoadedMedia
    player._seekable = True
    controller = _controller(player)

    controller.play(path, PlaybackInterval(1000, 1800))

    assert player.set_source_calls == []
    assert player.set_position_calls == [1000]
    assert player.play_calls == 1


def test_new_request_supersedes_loading_source_and_rejects_late_old_status(
    tmp_path: Path,
) -> None:
    player = FakePlayer()
    controller = _controller(player)
    old_path = (tmp_path / "old.wav").resolve()
    new_path = (tmp_path / "new.wav").resolve()
    controller.play(old_path, PlaybackInterval(1000, 1500))
    controller.play(new_path, PlaybackInterval(4000, 5000))

    player._source = QUrl.fromLocalFile(str(old_path))
    player._status = QMediaPlayer.MediaStatus.LoadedMedia
    player._seekable = True
    player.mediaStatusChanged.emit(player._status)
    assert player.play_calls == 0

    player._source = QUrl.fromLocalFile(str(new_path))
    player.sourceChanged.emit(player._source)
    assert player.set_position_calls == [4000]
    assert player.play_calls == 1
    assert controller._stop_at_ms == 5000


def test_stop_while_loading_prevents_late_autoplay(tmp_path: Path) -> None:
    player = FakePlayer()
    controller = _controller(player)
    controller.play(tmp_path / "audio.wav", PlaybackInterval(1000, 2000))
    controller.stop()
    player.finish_loading()

    assert controller._pending is None
    assert player.set_position_calls == []
    assert player.play_calls == 0


def test_media_error_clears_pending_without_playback(tmp_path: Path) -> None:
    player = FakePlayer()
    controller = _controller(player)
    errors = []
    controller.errorOccurred.connect(errors.append)
    controller.play(tmp_path / "audio.wav", PlaybackInterval(1000, 2000))
    player.errorOccurred.emit(QMediaPlayer.Error.FormatError, "bad media")

    assert controller._pending is None
    assert controller._stop_at_ms is None
    assert player.play_calls == 0
    assert errors == ["bad media"]


def test_seek_completion_and_end_boundary_are_state_driven(tmp_path: Path) -> None:
    player = FakePlayer()
    player.accept_seek = False
    controller = _controller(player)
    controller.play(tmp_path / "audio.wav", PlaybackInterval(2500, 3000))
    player.finish_loading()
    assert player.play_calls == 0

    player._position = 2500
    player.positionChanged.emit(2500)
    assert player.play_calls == 1
    player.positionChanged.emit(2999)
    assert controller._stop_at_ms == 3000
    player.positionChanged.emit(3000)
    assert controller._stop_at_ms is None
    assert player.stop_calls >= 2
