"""Bounded source-media playback for the read-only review workspace."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shiboken6 import Shiboken
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer

from lexbundler.application.alignment_review_service import ReviewUtterance


@dataclass(frozen=True, slots=True)
class PlaybackInterval:
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class _PendingPlayback:
    source: QUrl
    interval: PlaybackInterval


def speech_interval(item: ReviewUtterance) -> PlaybackInterval | None:
    if item.speech_start_ms is None or item.speech_end_ms is None:
        return None
    return PlaybackInterval(item.speech_start_ms, item.speech_end_ms)


def study_interval(item: ReviewUtterance) -> PlaybackInterval | None:
    speech = speech_interval(item)
    if speech is None:
        return None
    start = max(0, speech.start_ms - 50)
    end = speech.end_ms + 150
    if item.preceding_silence_start_ms is not None:
        start = max(start, item.preceding_silence_start_ms)
    if item.following_silence_end_ms is not None:
        end = min(end, item.following_silence_end_ms)
    return PlaybackInterval(start, end)


def context_interval(
    item: ReviewUtterance, *, before_ms: int = 1000, after_ms: int = 1000,
    duration_ms: int | None = None,
) -> PlaybackInterval | None:
    speech = speech_interval(item)
    if speech is None:
        return None
    end = speech.end_ms + after_ms
    if duration_ms is not None and duration_ms > 0:
        end = min(end, duration_ms)
    return PlaybackInterval(max(0, speech.start_ms - before_ms), end)


class PlaybackController(QObject):
    """Own QMediaPlayer and stop playback at a requested half-open boundary."""

    errorOccurred = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        player: QMediaPlayer | None = None,
        audio_output: QAudioOutput | None = None,
        media_devices: QMediaDevices | None = None,
        audio_output_factory: (
            Callable[[QAudioDevice, QObject], QAudioOutput] | None
        ) = None,
        audio_output_deleter: Callable[[QAudioOutput], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._player = player or QMediaPlayer(self)
        manage_default_output = (
            player is None or media_devices is not None
            or audio_output_factory is not None
        )
        self._media_devices = (
            media_devices or QMediaDevices(self) if manage_default_output else None
        )
        self._audio_output_factory = audio_output_factory or QAudioOutput
        self._audio_output_deleter = audio_output_deleter or Shiboken.delete
        self._audio_output = audio_output
        self._retired_audio_outputs: list[QAudioOutput] = []
        self._shutting_down = False
        if self._audio_output is None and self._media_devices is not None:
            self._audio_output = self._new_default_audio_output()
        if self._audio_output is not None:
            self._player.setAudioOutput(self._audio_output)
        self._pending: _PendingPlayback | None = None
        self._awaiting_seek = False
        self._stop_at_ms: int | None = None
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self.stop)
        self._player.positionChanged.connect(self._position_changed)
        self._player.mediaStatusChanged.connect(self._media_status_changed)
        self._player.seekableChanged.connect(self._seekable_changed)
        self._player.sourceChanged.connect(self._source_changed)
        self._player.errorOccurred.connect(self._player_error)
        if self._media_devices is not None:
            self._media_devices.audioOutputsChanged.connect(
                self._audio_outputs_changed
            )

    @property
    def duration_ms(self) -> int | None:
        duration = self._player.duration()
        return duration if duration > 0 else None

    def play(self, path: Path, interval: PlaybackInterval) -> None:
        if self._shutting_down:
            return
        self.stop()
        source = QUrl.fromLocalFile(str(Path(path).resolve()))
        self._pending = _PendingPlayback(source, interval)
        if self._player.source() != source:
            self._player.setSource(source)
        self._try_start_pending()

    def stop(self) -> None:
        self._stop_timer.stop()
        self._pending = None
        self._awaiting_seek = False
        self._stop_at_ms = None
        self._player.stop()

    def shutdown(self) -> None:
        """Deterministically sever native multimedia relationships before teardown."""
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._media_devices is not None:
            self._media_devices.audioOutputsChanged.disconnect(
                self._audio_outputs_changed
            )
        self.stop()
        self._player.setAudioOutput(None)

        outputs = [*self._retired_audio_outputs]
        if self._audio_output is not None:
            outputs.append(self._audio_output)
        self._retired_audio_outputs.clear()
        self._audio_output = None
        for output in outputs:
            if Shiboken.isValid(output):
                output.setParent(None)
                self._audio_output_deleter(output)

    def _new_default_audio_output(self) -> QAudioOutput:
        assert self._media_devices is not None
        return self._audio_output_factory(
            self._media_devices.defaultAudioOutput(), self
        )

    @staticmethod
    def _same_device(left: QAudioDevice, right: QAudioDevice) -> bool:
        return left.isNull() == right.isNull() and bytes(left.id()) == bytes(right.id())

    def _audio_outputs_changed(self) -> None:
        if self._shutting_down or self._media_devices is None:
            return
        default = self._media_devices.defaultAudioOutput()
        available = self._media_devices.audioOutputs()
        current = (
            self._audio_output.device() if self._audio_output is not None else None
        )
        current_is_default = (
            current is not None and self._same_device(current, default)
        )
        current_is_available = (
            current is not None
            and (
                current.isNull()
                or any(self._same_device(current, device) for device in available)
            )
        )
        if current_is_default and current_is_available:
            return

        volume = (
            self._audio_output.volume() if self._audio_output is not None else 1.0
        )
        previous = self._audio_output
        self.stop()
        self._player.setAudioOutput(None)
        if previous is not None:
            previous.setParent(None)
            self._retired_audio_outputs.append(previous)
            previous.deleteLater()
        replacement = self._new_default_audio_output()
        replacement.setVolume(volume)
        self._audio_output = replacement
        self._player.setAudioOutput(replacement)

    def _position_changed(self, position: int) -> None:
        if self._awaiting_seek and self._pending is not None:
            if position >= self._pending.interval.start_ms:
                self._activate_pending()
            return
        if self._stop_at_ms is not None and position >= self._stop_at_ms:
            self.stop()

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            if self._pending is not None or self._stop_at_ms is not None:
                self._fail("The source audio could not be loaded.")
            return
        self._try_start_pending()

    def _seekable_changed(self, seekable: bool) -> None:
        if seekable:
            self._try_start_pending()

    def _source_changed(self, _source: QUrl) -> None:
        self._try_start_pending()

    def _try_start_pending(self) -> None:
        pending = self._pending
        if (
            pending is None
            or self._awaiting_seek
            or self._player.source() != pending.source
            or not self._player.isSeekable()
            or self._player.mediaStatus() not in {
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.EndOfMedia,
            }
        ):
            return
        self._awaiting_seek = True
        self._player.setPosition(pending.interval.start_ms)
        if (
            self._pending is not None
            and self._player.position() >= pending.interval.start_ms
        ):
            self._activate_pending()

    def _activate_pending(self) -> None:
        pending = self._pending
        if pending is None or self._player.source() != pending.source:
            return
        self._pending = None
        self._awaiting_seek = False
        self._stop_at_ms = pending.interval.end_ms
        self._player.play()
        # positionChanged is authoritative; the timer is only a backend safety net.
        duration = pending.interval.end_ms - pending.interval.start_ms
        self._stop_timer.start(max(1, duration + 2000))

    def _player_error(self, _error, message: str) -> None:
        if self._pending is not None or self._stop_at_ms is not None:
            self._fail(message or "The source audio could not be played.")

    def _fail(self, message: str) -> None:
        self.stop()
        self.errorOccurred.emit(message)
