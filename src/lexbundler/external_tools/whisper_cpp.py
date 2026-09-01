"""Synchronous, schema-independent execution of whisper.cpp."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lexbundler.domain.errors import WhisperExecutionError

DIAGNOSTIC_TAIL_BYTES = 16 * 1024
TERMINATE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class WhisperCppExecutionRequest:
    executable_path: Path
    model_path: Path
    media_path: Path
    language: str
    output_base: Path


@dataclass(frozen=True, slots=True)
class WhisperCppExecutionResult:
    return_code: int
    produced_json_path: Path
    diagnostic_tail: str


def validate_whisper_cpp_inputs(
    executable_path: Path,
    model_path: Path,
    media_path: Path,
    language: str,
) -> tuple[Path, Path, Path, str]:
    """Resolve and validate required execution inputs without launching a child."""
    executable = _required_file(executable_path, "whisper.cpp executable")
    if not os.access(executable, os.X_OK):
        raise WhisperExecutionError(
            f"The whisper.cpp executable is not executable: {executable}"
        )
    model = _required_file(model_path, "Whisper model")
    media = _required_file(media_path, "Source media")
    if not isinstance(language, str) or not language.strip():
        raise WhisperExecutionError("Whisper language must be explicit and non-empty.")
    return executable, model, media, language.strip()


class WhisperCppRunner:
    """Run one whisper-cli process and enforce its native JSON contract."""

    def run(self, request: WhisperCppExecutionRequest) -> WhisperCppExecutionResult:
        executable, model, media, language = validate_whisper_cpp_inputs(
            request.executable_path,
            request.model_path,
            request.media_path,
            request.language,
        )
        output_base = Path(request.output_base).resolve()
        try:
            output_base.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise WhisperExecutionError(
                f"Could not create whisper.cpp staging directory: {output_base.parent}"
            ) from error
        produced_json = Path(f"{output_base}.json")
        diagnostic_path = output_base.parent / "whisper.log"
        argv = [
            str(executable),
            "-m",
            str(model),
            "-l",
            language,
            "-f",
            str(media),
            "-ojf",
            "-of",
            str(output_base),
        ]

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
                    raise WhisperExecutionError(
                        f"Could not launch whisper.cpp executable: {executable}"
                    ) from error
                try:
                    return_code = process.wait()
                except BaseException:
                    _stop_process(process)
                    raise
                diagnostics.flush()
                diagnostic_tail = _read_tail(diagnostics)
        except WhisperExecutionError:
            raise
        except OSError as error:
            raise WhisperExecutionError(
                "Could not create or read whisper.cpp staging files."
            ) from error

        if return_code != 0:
            raise WhisperExecutionError(
                _failure_message(
                    f"whisper.cpp exited with status {return_code}", diagnostic_tail
                )
            )
        if not produced_json.is_file():
            raise WhisperExecutionError(
                _failure_message(
                    "whisper.cpp exited successfully but produced no JSON file",
                    diagnostic_tail,
                )
            )
        try:
            if produced_json.stat().st_size == 0:
                raise WhisperExecutionError(
                    _failure_message(
                        "whisper.cpp produced an empty JSON file", diagnostic_tail
                    )
                )
        except OSError as error:
            raise WhisperExecutionError(
                "Could not inspect the JSON produced by whisper.cpp."
            ) from error
        return WhisperCppExecutionResult(
            return_code=return_code,
            produced_json_path=produced_json,
            diagnostic_tail=diagnostic_tail,
        )


def _required_file(path: Path, description: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise WhisperExecutionError(
            f"{description} does not exist or is not a regular file: {resolved}"
        )
    return resolved


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
