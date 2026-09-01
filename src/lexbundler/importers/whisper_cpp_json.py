"""Parse the whisper.cpp JSON subset required by LexBundler."""

import json
from dataclasses import dataclass
from pathlib import Path

from lexbundler.domain.corpus import JsonObject
from lexbundler.domain.errors import WhisperCppFormatError, WhisperImportError


@dataclass(frozen=True, slots=True)
class WhisperCppSegment:
    index: int
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class WhisperCppResult:
    language: str | None
    requested_language: str | None
    translate: bool | None
    system_info: str | None
    model_metadata: JsonObject | None
    model_path: str | None
    segments: tuple[WhisperCppSegment, ...]


def load_whisper_cpp_json(path: Path) -> WhisperCppResult:
    """Read and fully parse a whisper.cpp JSON artifact."""
    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise WhisperImportError(f"Could not read whisper.cpp JSON file: {path}") from error
    return parse_whisper_cpp_json(payload)


def parse_whisper_cpp_json(payload: bytes) -> WhisperCppResult:
    """Validate the required producer fields while tolerating unknown fields."""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WhisperCppFormatError("The file is not valid JSON.") from error

    if not isinstance(document, dict):
        raise WhisperCppFormatError("The whisper.cpp JSON root must be an object.")
    if "transcription" not in document:
        raise WhisperCppFormatError("Missing required transcription array.")
    transcription = document["transcription"]
    if not isinstance(transcription, list):
        raise WhisperCppFormatError("The transcription field must be an array.")

    segments = tuple(
        _parse_segment(segment, index) for index, segment in enumerate(transcription)
    )
    params = document.get("params")
    result = document.get("result")
    model = document.get("model")
    return WhisperCppResult(
        language=_optional_string_field(result, "language"),
        requested_language=_optional_string_field(params, "language"),
        translate=_optional_bool_field(params, "translate"),
        system_info=(
            document["systeminfo"]
            if isinstance(document.get("systeminfo"), str)
            else None
        ),
        model_metadata=dict(model) if isinstance(model, dict) else None,
        model_path=_optional_string_field(params, "model"),
        segments=segments,
    )


def _parse_segment(value: object, index: int) -> WhisperCppSegment:
    context = f"transcription[{index}]"
    if not isinstance(value, dict):
        raise WhisperCppFormatError(f"{context} must be an object.")
    if "text" not in value:
        raise WhisperCppFormatError(f"{context} is missing text.")
    text = value["text"]
    if not isinstance(text, str):
        raise WhisperCppFormatError(f"{context}.text must be a string.")
    if "offsets" not in value:
        raise WhisperCppFormatError(f"{context} is missing offsets.")
    offsets = value["offsets"]
    if not isinstance(offsets, dict):
        raise WhisperCppFormatError(f"{context}.offsets must be an object.")
    if "from" not in offsets or "to" not in offsets:
        raise WhisperCppFormatError(f"{context}.offsets requires from and to.")
    start_ms = offsets["from"]
    end_ms = offsets["to"]
    if type(start_ms) is not int or type(end_ms) is not int:
        raise WhisperCppFormatError(
            f"{context}.offsets.from and to must be integers."
        )
    if start_ms < 0:
        raise WhisperCppFormatError(f"{context}.offsets.from must be nonnegative.")
    if end_ms <= start_ms:
        raise WhisperCppFormatError(
            f"{context}.offsets.to must be greater than offsets.from."
        )
    return WhisperCppSegment(index, text, start_ms, end_ms)


def _optional_string_field(container: object, field: str) -> str | None:
    if not isinstance(container, dict):
        return None
    value = container.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _optional_bool_field(container: object, field: str) -> bool | None:
    if not isinstance(container, dict):
        return None
    value = container.get(field)
    return value if type(value) is bool else None

