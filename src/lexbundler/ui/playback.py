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
    ) -> None:
        super().__init__(parent)
        self._audio_output = audio_output or (QAudioOutput(self) if player is None else None)
        self._player = player or QMediaPlayer(self)
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

    @property
    def duration_ms(self) -> int | None:
        duration = self._player.duration()
        return duration if duration > 0 else None

    def play(self, path: Path, interval: PlaybackInterval) -> None:
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
