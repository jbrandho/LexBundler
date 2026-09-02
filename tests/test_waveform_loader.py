import threading
from pathlib import Path

import pytest

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from lexbundler.external_tools.ffmpeg_waveform import FfmpegWaveformResult
from lexbundler.ui.waveform_loader import WaveformLoader


class ControlledRunner:
    instances = []

    def __init__(self):
        self.cancelled = False
        self.release = threading.Event()
        self.request = None
        self.instances.append(self)

    def run(self, request):
        self.request = request
        self.release.wait(3)
        return FfmpegWaveformResult((-1.0, 0.0, 1.0), request.end_ms)

    def cancel(self):
        self.cancelled = True
        self.release.set()


def _process_events(milliseconds: int = 100) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def test_loader_runs_async_and_ignores_cancelled_stale_result(
    qapplication: QApplication, tmp_path: Path
) -> None:
    ControlledRunner.instances = []
    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"executable")
    source = tmp_path / "audio with spaces.wav"
    source.write_bytes(b"immutable")
    loader = WaveformLoader(
        executable_path=executable, runner_factory=ControlledRunner
    )
    loaded = []
    failed = []
    loader.loaded.connect(loaded.append)
    loader.failed.connect(failed.append)

    loader.request(asset_id=1, media_path=source, start_ms=0, end_ms=1000,
                   bucket_count=2)
    first = ControlledRunner.instances[-1]
    loader.request(asset_id=1, media_path=source, start_ms=2000, end_ms=5000,
                   bucket_count=2)
    second = ControlledRunner.instances[-1]
    assert first.cancelled
    second.release.set()
    _process_events()

    assert failed == []
    assert len(loaded) == 1
    assert (loaded[0].start_ms, loaded[0].end_ms) == (2000, 5000)
    assert len(loaded[0].buckets) == 2
    assert source.read_bytes() == b"immutable"


def test_loader_reports_missing_ffmpeg_and_missing_media(
    qapplication: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr("lexbundler.ui.waveform_loader.shutil.which", lambda _name: None)
    loader = WaveformLoader()
    loader.failed.connect(errors.append)
    media = tmp_path / "audio.wav"
    media.write_bytes(b"audio")
    loader.request(asset_id=1, media_path=media,
                   start_ms=0, end_ms=1000, bucket_count=10)
    assert errors == ["ffmpeg was not found on PATH."]
