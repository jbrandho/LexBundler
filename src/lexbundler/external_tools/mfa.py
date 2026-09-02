"""Synchronous, schema-independent Montreal Forced Aligner execution."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lexbundler.domain.errors import MfaExecutionError, MfaImportError
from lexbundler.importers.mfa_hf_json import load_mfa_hf_json

DIAGNOSTIC_TAIL_BYTES = 16 * 1024
TERMINATE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class MfaAlignmentRequest:
    executable_path: Path
    source_audio_path: Path
    transcript_path: Path
    model_id: str
    output_path: Path
    dialect: str
    use_g2p: bool = True
    output_format: str = "json"


@dataclass(frozen=True, slots=True)
class MfaAlignmentResult:
    return_code: int
    produced_json_path: Path
    diagnostic_tail: str


def validate_mfa_inputs(
    executable_path: Path, source_audio_path: Path, transcript_path: Path,
    model_id: str, dialect: str, use_g2p: bool, output_format: str,
) -> tuple[Path, Path, Path, str, str, bool, str]:
    executable = _required_file(executable_path, "MFA executable")
    if not os.access(executable, os.X_OK):
        raise MfaExecutionError(f"The MFA executable is not executable: {executable}")
    audio = _required_file(source_audio_path, "Source audio")
    transcript = _required_file(transcript_path, "Authoritative transcript")
    model = _required_text(model_id, "MFA model ID")
    normalized_dialect = _required_text(dialect, "MFA dialect")
    if type(use_g2p) is not bool:
        raise MfaExecutionError("MFA use_g2p must be a boolean.")
    normalized_format = _required_text(output_format, "MFA output format").lower()
    if normalized_format != "json":
        raise MfaExecutionError("M0.10 supports only MFA JSON output.")
    return (
        executable, audio, transcript, model, normalized_dialect, use_g2p,
        normalized_format,
    )


class MfaRunner:
    """Run one align_one_hf process and enforce its native JSON contract."""

    def run(self, request: MfaAlignmentRequest) -> MfaAlignmentResult:
        executable, audio, transcript, model, dialect, use_g2p, output_format = (
            validate_mfa_inputs(
                request.executable_path, request.source_audio_path,
                request.transcript_path, request.model_id, request.dialect,
                request.use_g2p, request.output_format,
            )
        )
        output = Path(request.output_path).resolve()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise MfaExecutionError(
                f"Could not create MFA staging directory: {output.parent}"
            ) from error
        argv = [
            str(executable), "align_one_hf", str(audio), str(transcript), model,
            str(output), "--dialect", dialect,
        ]
        if use_g2p:
            argv.append("--use_g2p")
        argv.extend(("--output_format", output_format))
        diagnostic_path = output.parent / "mfa.log"
        subprocess_environment = _subprocess_environment(executable)
        try:
            with diagnostic_path.open("w+b") as diagnostics:
                try:
                    process = subprocess.Popen(
                        argv, stdout=diagnostics, stderr=subprocess.STDOUT,
                        shell=False, env=subprocess_environment,
                    )
                except OSError as error:
                    raise MfaExecutionError(
                        f"Could not launch MFA executable: {executable}"
                    ) from error
                try:
                    return_code = process.wait()
                except BaseException:
                    _stop_process(process)
                    raise
                diagnostics.flush()
                diagnostic_tail = _read_tail(diagnostics)
        except MfaExecutionError:
            raise
        except OSError as error:
            raise MfaExecutionError(
                "Could not create or read MFA staging files."
            ) from error
        if return_code != 0:
            raise MfaExecutionError(_failure_message(
                f"MFA exited with status {return_code}", diagnostic_tail
            ))
        if not output.is_file():
            raise MfaExecutionError(_failure_message(
                "MFA exited successfully but produced no JSON file", diagnostic_tail
            ))
        try:
            if output.stat().st_size == 0:
                raise MfaExecutionError(_failure_message(
                    "MFA produced an empty JSON file", diagnostic_tail
                ))
        except OSError as error:
            raise MfaExecutionError("Could not inspect the JSON produced by MFA.") from error
        try:
            load_mfa_hf_json(output)
        except MfaImportError as error:
            raise MfaExecutionError(_failure_message(
                f"MFA produced invalid MFA JSON: {error}", diagnostic_tail
            )) from error
        return MfaAlignmentResult(return_code, output, diagnostic_tail)


def _subprocess_environment(executable: Path) -> dict[str, str]:
    """Copy the caller environment and expose sibling tool-suite executables."""
    environment = os.environ.copy()
    executable_directory = str(executable.parent)
    existing_path = environment.get("PATH", "")
    environment["PATH"] = (
        executable_directory
        if not existing_path
        else os.pathsep.join((executable_directory, existing_path))
    )
    return environment


def _required_file(path: Path, description: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise MfaExecutionError(
            f"{description} does not exist or is not a regular file: {resolved}"
        )
    return resolved


def _required_text(value: object, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MfaExecutionError(f"{description} must be explicit and non-empty.")
    return value.strip()


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
