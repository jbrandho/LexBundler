"""Asynchronous UI adapter for bounded offline waveform extraction."""

import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from lexbundler.application.waveform import WaveformError, WaveformWindow, build_envelope
from lexbundler.external_tools.ffmpeg_waveform import (
    FfmpegWaveformError,
    FfmpegWaveformRequest,
    FfmpegWaveformRunner,
)


class WaveformLoader(QObject):
    """Run bounded ffmpeg decoding off the UI thread and reject stale results."""

    loaded = Signal(object)
    failed = Signal(str)
    _completed = Signal(int, object, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        executable_path: Path | None = None,
        runner_factory: Callable[[], FfmpegWaveformRunner] = FfmpegWaveformRunner,
    ) -> None:
        super().__init__(parent)
        discovered = shutil.which("ffmpeg") if executable_path is None else executable_path
        self._executable_path = Path(discovered).resolve() if discovered else None
        self._runner_factory = runner_factory
        self._generation = 0
        self._runner: FfmpegWaveformRunner | None = None
        self._threads: set[threading.Thread] = set()
        self._completed.connect(self._deliver)

    def request(
        self,
        *,
        asset_id: int,
        media_path: Path,
        start_ms: int,
        end_ms: int,
        bucket_count: int,
    ) -> None:
        self.cancel()
        if self._executable_path is None:
            self.failed.emit("ffmpeg was not found on PATH.")
            return
        path = Path(media_path)
        if not path.is_file():
            self.failed.emit("The source audio is no longer available locally.")
            return
        if start_ms < 0 or end_ms <= start_ms or bucket_count <= 0:
            self.failed.emit("The requested waveform window is invalid.")
            return
        generation = self._generation
        runner = self._runner_factory()
        self._runner = runner
        thread = threading.Thread(
            target=self._run,
            args=(generation, runner, asset_id, path.resolve(), start_ms, end_ms,
                  bucket_count),
            name="lexbundler-waveform",
            daemon=True,
        )
        self._threads.add(thread)
        thread.start()

    def cancel(self) -> None:
        self._generation += 1
        runner = self._runner
        self._runner = None
        if runner is not None:
            runner.cancel()

    def _run(
        self, generation: int, runner: FfmpegWaveformRunner, asset_id: int,
        path: Path, start_ms: int, end_ms: int, bucket_count: int,
    ) -> None:
        try:
            result = runner.run(FfmpegWaveformRequest(
                self._executable_path or Path(), path, start_ms, end_ms
            ))
            buckets = build_envelope(list(result.samples), bucket_count)
            waveform = WaveformWindow(
                asset_id, path, start_ms, result.decoded_end_ms, buckets
            )
            self._completed.emit(generation, waveform, "")
        except (FfmpegWaveformError, WaveformError) as error:
            self._completed.emit(generation, None, str(error))
        finally:
            self._threads.discard(threading.current_thread())

    def _deliver(self, generation: int, waveform: object, error: str) -> None:
        if generation != self._generation:
            return
        self._runner = None
        if error:
            self.failed.emit(error)
        else:
            self.loaded.emit(waveform)
