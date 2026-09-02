"""Bounded source-media playback for the read-only review workspace."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from lexbundler.application.alignment_review_service import ReviewUtterance


@dataclass(frozen=True, slots=True)
class PlaybackInterval:
    start_ms: int
    end_ms: int


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

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._audio_output = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._stop_at_ms: int | None = None
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self.stop)
        self._player.positionChanged.connect(self._position_changed)
        self._player.errorOccurred.connect(self._player_error)

    @property
    def duration_ms(self) -> int | None:
        duration = self._player.duration()
        return duration if duration > 0 else None

    def play(self, path: Path, interval: PlaybackInterval) -> None:
        self.stop()
        self._stop_at_ms = interval.end_ms
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.setPosition(interval.start_ms)
        self._player.play()
        # positionChanged is authoritative; this is only a safety net for a backend
        # that stops reporting positions. Allow ample time for asynchronous loading.
        self._stop_timer.start(max(1, interval.end_ms - interval.start_ms + 2000))

    def stop(self) -> None:
        self._stop_timer.stop()
        self._stop_at_ms = None
        self._player.stop()

    def _position_changed(self, position: int) -> None:
        if self._stop_at_ms is not None and position >= self._stop_at_ms:
            self.stop()

    def _player_error(self, _error, message: str) -> None:
        self.stop()
        self.errorOccurred.emit(message or "The source audio could not be played.")
