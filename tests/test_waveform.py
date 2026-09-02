import pytest

from lexbundler.application.waveform import WaveformError, build_envelope


def test_envelope_preserves_positive_and_negative_peaks() -> None:
    buckets = build_envelope([0.0, -1.0, 0.25, 0.9, -0.1, 0.0], 2)
    assert (buckets[0].minimum, buckets[0].maximum) == (-1.0, 0.25)
    assert (buckets[1].minimum, buckets[1].maximum) == (-0.1, 0.9)


def test_silence_and_very_short_audio_are_supported() -> None:
    silence = build_envelope([0.0] * 20, 5)
    assert all(bucket.minimum == bucket.maximum == 0.0 for bucket in silence)
    tiny = build_envelope([0.75], 300)
    assert len(tiny) == 1
    assert tiny[0].maximum == 0.75
def test_empty_audio_fails_cleanly() -> None:
    with pytest.raises(WaveformError):
        build_envelope([], 10)
