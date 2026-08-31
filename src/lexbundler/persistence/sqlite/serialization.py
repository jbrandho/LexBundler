"""SQLite text serialization for UTC timestamps and JSON objects."""

import json
from datetime import UTC, datetime

from lexbundler.domain.corpus import JsonObject
from lexbundler.domain.errors import InvalidCorpusDataError


def format_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Timestamps must be RFC 3339 UTC text.")
    parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    if parsed.tzinfo is None:
        raise ValueError("Timestamp is ambiguous.")
    return parsed.astimezone(UTC)


def dump_json(value: JsonObject) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as error:
        raise InvalidCorpusDataError("Metadata must contain valid JSON values.") from error


def load_json(value: object) -> JsonObject:
    if not isinstance(value, str):
        raise ValueError("JSON storage value must be text.")
    decoded = json.loads(value)
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) for key in decoded
    ):
        raise ValueError("Metadata JSON must contain an object.")
    return decoded

