"""Tests for the producer-native whisper.cpp JSON adapter."""

import json
import re

import pytest

from lexbundler.domain.errors import WhisperCppFormatError
from lexbundler.importers.whisper_cpp_json import parse_whisper_cpp_json


def _payload(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode()


def _segment(
    text: object = "你好", start: object = 10, end: object = 20, **extra: object
) -> dict[str, object]:
    return {
        "text": text,
        "offsets": {"from": start, "to": end},
        **extra,
    }


def test_observed_format_preserves_order_text_offsets_and_metadata() -> None:
    document = {
        "systeminfo": "synthetic host",
        "model": {"type": "large", "unused": 9},
        "params": {
            "model": "/models/arbitrary-name.bin",
            "language": "zh",
            "translate": False,
        },
        "result": {"language": "zh"},
        "unknown": {"future": True},
        "transcription": [
            _segment(
                "你好",
                920,
                5060,
                tokens=[{"text": "WRONG"}, {"text": "[_TT_253]"}],
                future_field="ignored",
            ),
            _segment(" English text", 5060, 6100),
            _segment("", 6100, 6200),
        ],
    }

    result = parse_whisper_cpp_json(_payload(document))

    assert [segment.index for segment in result.segments] == [0, 1, 2]
    assert [segment.text for segment in result.segments] == [
        "你好",
        " English text",
        "",
    ]
    assert [(segment.start_ms, segment.end_ms) for segment in result.segments] == [
        (920, 5060),
        (5060, 6100),
        (6100, 6200),
    ]
    assert result.language == "zh"
    assert result.requested_language == "zh"
    assert result.translate is False
    assert result.model_metadata == {"type": "large", "unused": 9}
    assert result.model_path == "/models/arbitrary-name.bin"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{", "valid JSON"),
        (_payload([]), "root must be an object"),
        (_payload({}), "Missing required transcription"),
        (_payload({"transcription": {}}), "must be an array"),
        (_payload({"transcription": [3]}), "transcription[0] must be an object"),
        (_payload({"transcription": [{}]}), "missing text"),
        (_payload({"transcription": [_segment(text=7)]}), "text must be a string"),
        (
            _payload({"transcription": [{"text": "x"}]}),
            "missing offsets",
        ),
        (
            _payload({"transcription": [{"text": "x", "offsets": []}]}),
            "offsets must be an object",
        ),
        (
            _payload({"transcription": [{"text": "x", "offsets": {"from": 0}}]}),
            "requires from and to",
        ),
        (_payload({"transcription": [_segment(start=True)]}), "must be integers"),
        (_payload({"transcription": [_segment(end=False)]}), "must be integers"),
        (_payload({"transcription": [_segment(start=1.5)]}), "must be integers"),
        (_payload({"transcription": [_segment(end=2.5)]}), "must be integers"),
        (_payload({"transcription": [_segment(start=-1)]}), "nonnegative"),
        (_payload({"transcription": [_segment(start=10, end=10)]}), "greater than"),
        (_payload({"transcription": [_segment(start=10, end=9)]}), "greater than"),
    ],
)
def test_malformed_required_data_is_rejected(payload: bytes, message: str) -> None:
    with pytest.raises(WhisperCppFormatError, match=re.escape(message)):
        parse_whisper_cpp_json(payload)


def test_empty_text_and_absent_optional_producer_fields_are_valid() -> None:
    result = parse_whisper_cpp_json(
        _payload({"transcription": [_segment("", 0, 1)]})
    )

    assert result.segments[0].text == ""
    assert result.language is None
    assert result.translate is None
