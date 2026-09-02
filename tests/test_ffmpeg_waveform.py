import json
import struct
import threading
from pathlib import Path

import pytest

import lexbundler.external_tools.ffmpeg_waveform as module
from lexbundler.external_tools.ffmpeg_waveform import (
    FfmpegWaveformError,
    FfmpegWaveformRequest,
    FfmpegWaveformRunner,
)


def _fake_ffmpeg(tmp_path: Path, *, fail: bool = False) -> tuple[Path, Path]:
    directory = tmp_path / "ffmpeg with spaces"
    directory.mkdir()
    executable = directory / "fake ffmpeg"
    capture = tmp_path / "captured arguments.json"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, struct, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        + ("print('bad media', file=sys.stderr)\nraise SystemExit(7)\n" if fail else
           "args=sys.argv[1:]\n"
           "duration=float(args[args.index('-t')+1])\n"
           "rate=int(args[args.index('-ar')+1])\n"
           "sys.stdout.buffer.write(b''.join(struct.pack('<f', (-1, 0, 1)[i % 3]) for i in range(round(duration*rate))))\n")
    )
    executable.chmod(0o755)
    return executable, capture


def test_runner_uses_bounded_explicit_argv_shell_false_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    source = tmp_path / "source media with spaces.mp3"
    source.write_bytes(b"immutable")
    original = source.read_bytes()
    real_popen = module.subprocess.Popen
    calls = []

    def checked_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", checked_popen)
    result = FfmpegWaveformRunner().run(
        FfmpegWaveformRequest(executable, source, 1250, 3500)
    )

    argv = json.loads(capture.read_text())
    assert argv[argv.index("-ss") + 1] == "1.250"
    assert argv[argv.index("-t") + 1] == "2.250"
    assert argv[argv.index("-i") + 1] == str(source.resolve())
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[-4:] == ["pcm_f32le", "-f", "f32le", "pipe:1"]
    assert calls[0][1]["shell"] is False
    assert len(result.samples) == 36_000
    assert result.decoded_end_ms == 3500
    assert min(result.samples) == -1 and max(result.samples) == 1
    assert source.read_bytes() == original


def test_runner_reports_malformed_media_and_bounds_output(tmp_path: Path) -> None:
    executable, _capture = _fake_ffmpeg(tmp_path, fail=True)
    source = tmp_path / "bad.mp3"
    source.write_bytes(b"bad")
    with pytest.raises(FfmpegWaveformError, match="status 7.*bad media"):
        FfmpegWaveformRunner().run(
            FfmpegWaveformRequest(executable, source, 0, 1000)
        )

    with pytest.raises(FfmpegWaveformError, match="at most one minute"):
        FfmpegWaveformRunner().run(
            FfmpegWaveformRequest(executable, source, 0, 60_001)
        )


@pytest.mark.parametrize("duration_ms", [1000, 3000, 6000, 10_000])
def test_runner_output_is_bounded_by_window_duration(
    tmp_path: Path, duration_ms: int
) -> None:
    executable, _capture = _fake_ffmpeg(tmp_path)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    result = FfmpegWaveformRunner().run(
        FfmpegWaveformRequest(executable, source, 500, 500 + duration_ms)
    )
    assert len(result.samples) == duration_ms * 16
    assert result.decoded_end_ms == 500 + duration_ms


def test_runner_can_cancel_an_active_process(tmp_path: Path) -> None:
    executable = tmp_path / "slow ffmpeg"
    executable.write_text(
        "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n"
    )
    executable.chmod(0o755)
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    runner = FfmpegWaveformRunner()
    errors = []
    thread = threading.Thread(
        target=lambda: _capture_error(
            errors, runner,
            FfmpegWaveformRequest(executable, source, 0, 1000),
        )
    )
    thread.start()
    for _ in range(1000):
        if runner._process is not None:
            break
        threading.Event().wait(0.001)
    runner.cancel()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors and "cancelled" in str(errors[0])


def _capture_error(errors, runner, request) -> None:
    try:
        runner.run(request)
    except FfmpegWaveformError as error:
        errors.append(error)
