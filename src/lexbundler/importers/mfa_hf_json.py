"""Pure parser for Montreal Forced Aligner 3.4 HF JSON artifacts."""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from lexbundler.domain.errors import MfaFormatError, MfaImportError


@dataclass(frozen=True, slots=True)
class MfaInterval:
    start_ms: int
    end_ms: int
    label: str
    is_silence: bool


@dataclass(frozen=True, slots=True)
class MfaHfResult:
    start_ms: int
    end_ms: int
    words: tuple[MfaInterval, ...]
    phones: tuple[MfaInterval, ...]


def load_mfa_hf_json(path: Path) -> MfaHfResult:
    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise MfaImportError(f"Could not read MFA JSON file: {path}") from error
    return parse_mfa_hf_json(payload)


def parse_mfa_hf_json(payload: object) -> MfaHfResult:
    """Validate and convert an MFA HF document without mutating it."""
    if isinstance(payload, (bytes, str)):
        try:
            document = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise MfaFormatError("The file is not valid JSON.") from error
    else:
        document = payload
    if not isinstance(document, dict):
        raise MfaFormatError("The MFA JSON root must be an object.")
    start = _seconds(document.get("start"), "start")
    end = _seconds(document.get("end"), "end")
    if end < start:
        raise MfaFormatError("Top-level end must not precede start.")
    tiers = document.get("tiers")
    if not isinstance(tiers, dict):
        raise MfaFormatError("Missing required tiers object.")
    words = _tier(tiers.get("words"), "words", start, end, "<eps>")
    phones = _tier(tiers.get("phones"), "phones", start, end, "sil")
    return MfaHfResult(_milliseconds(start), _milliseconds(end), words, phones)


def _tier(
    value: object, name: str, document_start: float, document_end: float,
    silence_label: str,
) -> tuple[MfaInterval, ...]:
    if not isinstance(value, dict):
        raise MfaFormatError(f"Missing required tiers.{name} object.")
    if value.get("type") != "IntervalTier":
        raise MfaFormatError(f"tiers.{name}.type must be IntervalTier.")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise MfaFormatError(f"tiers.{name}.entries must be an array.")
    parsed: list[MfaInterval] = []
    for index, entry in enumerate(entries):
        context = f"tiers.{name}.entries[{index}]"
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise MfaFormatError(f"{context} must contain exactly three values.")
        start = _seconds(entry[0], f"{context}[0]")
        end = _seconds(entry[1], f"{context}[1]")
        label = entry[2]
        if not isinstance(label, str):
            raise MfaFormatError(f"{context}[2] must be a string.")
        if end < start:
            raise MfaFormatError(f"{context} end must not precede start.")
        if start < document_start or end > document_end:
            raise MfaFormatError(f"{context} lies outside the document bounds.")
        parsed.append(MfaInterval(
            _milliseconds(start), _milliseconds(end), label,
            label == silence_label,
        ))
    return tuple(parsed)


def _seconds(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MfaFormatError(f"{field} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise MfaFormatError(f"{field} must be a finite number.")
    if result < 0:
        raise MfaFormatError(f"{field} must be nonnegative.")
    return result


def _milliseconds(seconds: float) -> int:
    return round(seconds * 1000)
