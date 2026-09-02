"""Bounded offline waveform PCM decoding through ffmpeg."""

import os
import struct
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile


ANALYSIS_SAMPLE_RATE = 16_000
MAX_WINDOW_MS = 60 * 1000
DIAGNOSTIC_TAIL_BYTES = 16 * 1024


class FfmpegWaveformError(Exception):
    """ffmpeg could not produce bounded waveform-analysis PCM."""


@dataclass(frozen=True, slots=True)
class FfmpegWaveformRequest:
    executable_path: Path
    input_media_path: Path
    start_ms: int
    end_ms: int
    sample_rate: int = ANALYSIS_SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class FfmpegWaveformResult:
    samples: tuple[float, ...]
    decoded_end_ms: int


class FfmpegWaveformRunner:
    """Decode one bounded media window to mono float PCM without a shell."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = False

    def run(self, request: FfmpegWaveformRequest) -> FfmpegWaveformResult:
        executable = _required_file(request.executable_path, "ffmpeg executable")
        if not os.access(executable, os.X_OK):
            raise FfmpegWaveformError(
                f"The ffmpeg executable is not executable: {executable}"
            )
        media = _required_file(request.input_media_path, "source media")
        duration_ms = request.end_ms - request.start_ms
        if (
            type(request.start_ms) is not int
            or type(request.end_ms) is not int
            or request.start_ms < 0
            or duration_ms <= 0
            or duration_ms > MAX_WINDOW_MS
        ):
            raise FfmpegWaveformError(
                "Waveform bounds must be a positive window of at most one minute."
            )
        if request.sample_rate <= 0:
            raise FfmpegWaveformError("Waveform analysis sample rate must be positive.")
        argv = [
            str(executable),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            _format_milliseconds(request.start_ms),
            "-t",
            _format_milliseconds(duration_ms),
            "-i",
            str(media),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(request.sample_rate),
            "-c:a",
            "pcm_f32le",
            "-f",
            "f32le",
            "pipe:1",
        ]
        maximum_bytes = (duration_ms * request.sample_rate // 1000 + 1) * 4
        with TemporaryFile() as pcm, TemporaryFile() as diagnostics:
            try:
                process = subprocess.Popen(
                    argv,
                    stdout=pcm,
                    stderr=diagnostics,
                    shell=False,
                )
            except OSError as error:
                raise FfmpegWaveformError(
                    f"Could not launch ffmpeg executable: {executable}"
                ) from error
            with self._lock:
                self._process = process
                cancelled = self._cancelled
            if cancelled:
                _stop_process(process)
            try:
                return_code = process.wait()
            finally:
                with self._lock:
                    if self._process is process:
                        self._process = None
            if self._cancelled:
                raise FfmpegWaveformError("Waveform decoding was cancelled.")
            diagnostics.flush()
            diagnostic_tail = _read_tail(diagnostics)
            if return_code != 0:
                message = f"ffmpeg exited with status {return_code}"
                if diagnostic_tail:
                    message += f": {diagnostic_tail}"
                raise FfmpegWaveformError(message)
            pcm.flush()
            size = pcm.tell()
            if size > maximum_bytes:
                raise FfmpegWaveformError(
                    "ffmpeg produced more PCM than the bounded request permits."
                )
            if size == 0 or size % 4:
                raise FfmpegWaveformError(
                    "ffmpeg produced no usable float waveform samples."
                )
            pcm.seek(0)
            payload = pcm.read()
        samples = tuple(value[0] for value in struct.iter_unpack("<f", payload))
        decoded_duration_ms = round(len(samples) * 1000 / request.sample_rate)
        return FfmpegWaveformResult(
            samples, min(request.end_ms, request.start_ms + decoded_duration_ms)
        )

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                threading.Thread(
                    target=_ensure_stopped,
                    args=(process,),
                    name="lexbundler-waveform-cancel",
                    daemon=True,
                ).start()


def _required_file(path: Path, description: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FfmpegWaveformError(
            f"The {description} does not exist or is not a regular file: {resolved}"
        )
    return resolved


def _format_milliseconds(milliseconds: int) -> str:
    seconds, remainder = divmod(milliseconds, 1000)
    return f"{seconds}.{remainder:03d}"


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _ensure_stopped(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _read_tail(stream: object) -> str:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(max(0, size - DIAGNOSTIC_TAIL_BYTES))
    return stream.read().decode("utf-8", errors="replace").strip()
