import pytest

from lexbundler.application.provisional_boundaries import ProvisionalBoundaryModel


def _model(**changes) -> ProvisionalBoundaryModel:
    values = {
        "speech_start_ms": 1000,
        "speech_end_ms": 2000,
        "visible_start_ms": 0,
        "visible_end_ms": 3000,
        "preceding_silence_start_ms": 900,
        "following_silence_end_ms": 2200,
    }
    values.update(changes)
    return ProvisionalBoundaryModel(**values)


def test_default_preview_and_known_silence_clamping() -> None:
    assert _model().current.start_ms == 950
    assert _model().current.end_ms == 2150
    constrained = _model(
        preceding_silence_start_ms=980, following_silence_end_ms=2070
    )
    assert (constrained.current.start_ms, constrained.current.end_ms) == (980, 2070)


def test_default_clamps_at_zero_and_visible_edges() -> None:
    model = _model(
        speech_start_ms=20, speech_end_ms=100,
        visible_start_ms=0, visible_end_ms=180,
        preceding_silence_start_ms=None, following_silence_end_ms=None,
    )
    assert (model.current.start_ms, model.current.end_ms) == (0, 180)


@pytest.mark.parametrize("amount", [-50, -10, 10, 50])
def test_start_nudges(amount: int) -> None:
    model = _model()
    assert model.nudge_start(amount).start_ms == 950 + amount


@pytest.mark.parametrize("amount", [-50, -10, 10, 50])
def test_end_nudges(amount: int) -> None:
    model = _model()
    assert model.nudge_end(amount).end_ms == 2150 + amount


def test_boundaries_cannot_cross_and_reset_restores_default() -> None:
    model = _model()
    model.set_start(9999)
    assert model.current.start_ms == model.current.end_ms - 1
    model.set_end(-1)
    assert model.current.end_ms == model.current.start_ms + 1
    model.reset()
    assert model.current == model.default
    assert model.speech.start_ms == 1000
    assert model.speech.end_ms == 2000


def test_new_utterance_has_independent_default_state() -> None:
    first = _model()
    first.nudge_start(50)
    second = _model(speech_start_ms=4000, speech_end_ms=5000,
                    visible_start_ms=3000, visible_end_ms=6000,
                    preceding_silence_start_ms=None,
                    following_silence_end_ms=None)
    assert second.current.start_ms == 3950
    assert first.current != first.default

