"""Synchronous, schema-independent ffmpeg audio clip rendering."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lexbundler.domain.errors import MediaRenderError

DIAGNOSTIC_TAIL_BYTES = 16 * 1024
TERMINATE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class FfmpegClipRequest:
    executable_path: Path
    input_media_path: Path
    source_start_ms: int
    source_end_ms: int
    output_path: Path


@dataclass(frozen=True, slots=True)
class FfmpegClipResult:
    return_code: int
    output_path: Path
    diagnostic_tail: str


def validate_ffmpeg_inputs(
    executable_path: Path, input_media_path: Path
) -> tuple[Path, Path]:
    executable = _required_file(executable_path, "ffmpeg executable")
    if not os.access(executable, os.X_OK):
        raise MediaRenderError(f"The ffmpeg executable is not executable: {executable}")
    media = _required_file(input_media_path, "Source media")
    return executable, media


class FfmpegClipRunner:
    """Render one accurately decoded/re-encoded MP3 clip."""

    def run(self, request: FfmpegClipRequest) -> FfmpegClipResult:
        executable, media = validate_ffmpeg_inputs(
            request.executable_path, request.input_media_path
        )
        _validate_range(request.source_start_ms, request.source_end_ms)
        output = Path(request.output_path).resolve()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise MediaRenderError(
                f"Could not create ffmpeg staging directory: {output.parent}"
            ) from error
        duration_ms = request.source_end_ms - request.source_start_ms
        argv = [
            str(executable),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-i",
            str(media),
            "-ss",
            _format_milliseconds(request.source_start_ms),
            "-t",
            _format_milliseconds(duration_ms),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(output),
        ]
        diagnostic_path = output.parent / "ffmpeg.log"
        try:
            with diagnostic_path.open("w+b") as diagnostics:
                try:
                    process = subprocess.Popen(
                        argv,
                        stdout=diagnostics,
                        stderr=subprocess.STDOUT,
                        shell=False,
                    )
                except OSError as error:
                    raise MediaRenderError(
                        f"Could not launch ffmpeg executable: {executable}"
                    ) from error
                try:
                    return_code = process.wait()
                except BaseException:
                    _stop_process(process)
                    raise
                diagnostics.flush()
                diagnostic_tail = _read_tail(diagnostics)
        except MediaRenderError:
            raise
        except OSError as error:
            raise MediaRenderError(
                "Could not create or read ffmpeg staging files."
            ) from error

        if return_code != 0:
            raise MediaRenderError(
                _failure_message(
                    f"ffmpeg exited with status {return_code}", diagnostic_tail
                )
            )
        if not output.is_file():
            raise MediaRenderError(
                _failure_message(
                    "ffmpeg exited successfully but produced no clip", diagnostic_tail
                )
            )
        try:
            if output.stat().st_size == 0:
                raise MediaRenderError(
                    _failure_message("ffmpeg produced an empty clip", diagnostic_tail)
                )
        except OSError as error:
            raise MediaRenderError("Could not inspect the clip produced by ffmpeg.") from error
        return FfmpegClipResult(return_code, output, diagnostic_tail)


def _required_file(path: Path, description: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise MediaRenderError(
            f"{description} does not exist or is not a regular file: {resolved}"
        )
    return resolved


def _validate_range(start_ms: int, end_ms: int) -> None:
    if type(start_ms) is not int or type(end_ms) is not int:
        raise MediaRenderError("ffmpeg clip offsets must be integer milliseconds.")
    if start_ms < 0 or end_ms <= start_ms:
        raise MediaRenderError(
            "ffmpeg clip offsets must form a non-empty range with a nonnegative start."
        )


def _format_milliseconds(milliseconds: int) -> str:
    seconds, remainder = divmod(milliseconds, 1000)
    return f"{seconds}.{remainder:03d}"


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=TERMINATE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _read_tail(diagnostics: object) -> str:
    diagnostics.seek(0, os.SEEK_END)
    size = diagnostics.tell()
    diagnostics.seek(max(0, size - DIAGNOSTIC_TAIL_BYTES))
    return diagnostics.read().decode("utf-8", errors="replace").strip()


def _failure_message(summary: str, diagnostic_tail: str) -> str:
    if not diagnostic_tail:
        return f"{summary}."
    return f"{summary}. Diagnostic tail:\n{diagnostic_tail}"
