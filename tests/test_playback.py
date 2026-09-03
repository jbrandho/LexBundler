from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
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
        self.audio_outputs = []
        self.events = []
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
        self.events.append("stop")

    def setAudioOutput(self, output):
        self.audio_outputs.append(output)
        self.events.append("detach" if output is None else "attach")

    def finish_loading(self):
        self._status = QMediaPlayer.MediaStatus.LoadedMedia
        self.mediaStatusChanged.emit(self._status)
        self._seekable = True
        self.seekableChanged.emit(True)


def _controller(player: FakePlayer) -> PlaybackController:
    return PlaybackController(player=player)


class FakeDevice:
    def __init__(self, identifier: str, description: str = "") -> None:
        self._identifier = identifier
        self._description = description or identifier

    def id(self):
        return QByteArray(self._identifier.encode())

    def isNull(self):
        return not self._identifier

    def description(self):
        return self._description


class FakeMediaDevices(QObject):
    audioOutputsChanged = Signal()

    def __init__(self, default: FakeDevice, outputs: list[FakeDevice]) -> None:
        super().__init__()
        self.default = default
        self.outputs = outputs

    def defaultAudioOutput(self):
        return self.default

    def audioOutputs(self):
        return self.outputs

    def change(self, default: FakeDevice, outputs: list[FakeDevice]) -> None:
        self.default = default
        self.outputs = outputs
        self.audioOutputsChanged.emit()


class FakeAudioOutput(QObject):
    def __init__(self, device: FakeDevice, _parent: QObject) -> None:
        super().__init__()
        self._device = device
        self._volume = 1.0

    def device(self):
        return self._device

    def volume(self):
        return self._volume

    def setVolume(self, volume):
        self._volume = volume


def _managed_controller(player, devices, *, deleter=None):
    created = []

    def create_output(device, parent):
        output = FakeAudioOutput(device, parent)
        created.append(output)
        return output

    controller = PlaybackController(
        player=player,
        media_devices=devices,
        audio_output_factory=create_output,
        audio_output_deleter=deleter,
    )
    return controller, created


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


def test_initial_construction_uses_current_default_output() -> None:
    speakers = FakeDevice("speakers", "Mac Speakers")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()

    controller, created = _managed_controller(player, devices)

    assert controller._audio_output is created[0]
    assert created[0].device() is speakers
    assert player.audio_outputs == [created[0]]
    assert player.play_calls == 0


def test_default_output_change_recreates_and_attaches_without_playback() -> None:
    speakers = FakeDevice("speakers")
    airpods = FakeDevice("airpods")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    controller, created = _managed_controller(player, devices)
    created[0].setVolume(0.37)

    devices.change(airpods, [speakers, airpods])

    assert len(created) == 2
    assert controller._audio_output is created[1]
    assert created[1].device() is airpods
    assert created[1].volume() == 0.37
    assert player.audio_outputs == [created[0], None, created[1]]
    assert player.stop_calls == 1
    assert player.play_calls == 0


def test_disappearing_output_cannot_remain_selected() -> None:
    airpods = FakeDevice("airpods")
    speakers = FakeDevice("speakers")
    devices = FakeMediaDevices(airpods, [airpods, speakers])
    player = FakePlayer()
    controller, created = _managed_controller(player, devices)

    devices.change(speakers, [speakers])

    assert controller._audio_output.device() is speakers
    assert created[0].device() is airpods
    assert created[0] is not controller._audio_output


def test_unchanged_available_default_does_not_interrupt_playback() -> None:
    speakers = FakeDevice("speakers")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    controller, created = _managed_controller(player, devices)

    devices.change(FakeDevice("speakers"), [FakeDevice("speakers")])

    assert len(created) == 1
    assert controller._audio_output is created[0]
    assert player.stop_calls == 0


def test_pending_playback_is_cancelled_safely_across_device_change(
    tmp_path: Path,
) -> None:
    speakers = FakeDevice("speakers")
    airpods = FakeDevice("airpods")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    controller, _created = _managed_controller(player, devices)
    controller.play(tmp_path / "audio.wav", PlaybackInterval(1000, 2000))

    devices.change(airpods, [airpods])
    player.finish_loading()

    assert controller._pending is None
    assert controller._stop_at_ms is None
    assert player.set_position_calls == []
    assert player.play_calls == 0


def test_device_change_stops_active_interval_without_resuming(tmp_path: Path) -> None:
    speakers = FakeDevice("speakers")
    airpods = FakeDevice("airpods")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    player._status = QMediaPlayer.MediaStatus.LoadedMedia
    player._seekable = True
    controller, _created = _managed_controller(player, devices)
    controller.play(tmp_path / "audio.wav", PlaybackInterval(1200, 1900))
    player.finish_loading()
    assert player.play_calls == 1

    devices.change(airpods, [airpods])

    assert controller._pending is None
    assert controller._stop_at_ms is None
    assert player.play_calls == 1
    assert player.stop_calls >= 2


def test_shutdown_detaches_before_disposing_current_and_retired_outputs() -> None:
    speakers = FakeDevice("speakers")
    airpods = FakeDevice("airpods")
    display = FakeDevice("display")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    disposed = []

    def dispose(output):
        player.events.append("dispose")
        disposed.append(output)

    controller, created = _managed_controller(
        player, devices, deleter=dispose
    )
    devices.change(airpods, [airpods])
    devices.change(display, [display])
    player.events.clear()

    controller.shutdown()

    assert player.events == ["stop", "detach", "dispose", "dispose", "dispose"]
    assert disposed == created
    assert controller._audio_output is None
    assert controller._retired_audio_outputs == []


def test_shutdown_is_idempotent_and_device_notifications_become_inert() -> None:
    speakers = FakeDevice("speakers")
    airpods = FakeDevice("airpods")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    disposed = []
    controller, created = _managed_controller(
        player, devices, deleter=disposed.append
    )

    controller.shutdown()
    calls_after_shutdown = (
        player.stop_calls, tuple(player.audio_outputs), tuple(disposed)
    )
    controller.shutdown()
    devices.change(airpods, [airpods])

    assert (player.stop_calls, tuple(player.audio_outputs), tuple(disposed)) == (
        calls_after_shutdown
    )
    assert disposed == created


def test_playback_state_machine_works_after_output_replacement(
    tmp_path: Path,
) -> None:
    speakers = FakeDevice("speakers")
    airpods = FakeDevice("airpods")
    devices = FakeMediaDevices(speakers, [speakers])
    player = FakePlayer()
    controller, _created = _managed_controller(player, devices)
    devices.change(airpods, [airpods])

    controller.play(tmp_path / "audio.wav", PlaybackInterval(1200, 1900))
    player.finish_loading()

    assert player.set_position_calls == [1200]
    assert player.play_calls == 1
    assert controller._stop_at_ms == 1900


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
