import copy
import json

import pytest

from lexbundler.domain.errors import MfaFormatError
from lexbundler.importers.mfa_hf_json import parse_mfa_hf_json


def _document() -> dict[str, object]:
    return {
        "start": 0,
        "end": 22.0,
        "tiers": {
            "words": {"type": "IntervalTier", "entries": [
                [0.0, 2.66, "<eps>"], [2.66, 3.13, "周末"],
                [16.059999, 17.780001, "要不"],
                [17.780001, 18.629999, "要"],
            ]},
            "phones": {"type": "IntervalTier", "entries": [
                [0.0, 2.66, "sil"], [2.66, 3.13, "ʈʂʰɤʊ"],
            ]},
        },
    }


def test_real_shape_preserves_labels_order_silence_and_rounding() -> None:
    document = _document()
    original = copy.deepcopy(document)
    result = parse_mfa_hf_json(document)
    assert document == original
    assert [entry.label for entry in result.words] == ["<eps>", "周末", "要不", "要"]
    assert [entry.is_silence for entry in result.words] == [True, False, False, False]
    assert [entry.label for entry in result.phones] == ["sil", "ʈʂʰɤʊ"]
    assert result.phones[0].is_silence is True
    assert [(entry.start_ms, entry.end_ms) for entry in result.words[2:]] == [
        (16060, 17780), (17780, 18630)
    ]


def test_accepts_json_bytes_and_text() -> None:
    payload = json.dumps(_document(), ensure_ascii=False)
    assert parse_mfa_hf_json(payload.encode()) == parse_mfa_hf_json(payload)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["tiers"]["words"].update(entries=[[0, 1]]), "exactly three"),
        (lambda d: d.update(start=True), "finite number"),
        (lambda d: d.update(end=float("nan")), "finite number"),
        (lambda d: d.update(end=float("inf")), "finite number"),
        (lambda d: d.update(start=-1), "nonnegative"),
        (lambda d: d["tiers"]["words"].update(entries=[[2, 1, "x"]]), "must not precede"),
        (lambda d: d["tiers"]["words"].update(entries=[[0, 23, "x"]]), "outside"),
        (lambda d: d["tiers"]["words"].update(type="PointTier"), "IntervalTier"),
        (lambda d: d["tiers"].pop("phones"), "phones"),
    ],
)
def test_invalid_documents_are_rejected(mutate: object, message: str) -> None:
    document = _document()
    mutate(document)  # type: ignore[operator]
    with pytest.raises(MfaFormatError, match=message):
        parse_mfa_hf_json(document)

