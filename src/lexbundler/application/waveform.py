"""Immutable bounded waveform projections and PCM envelope construction."""

from dataclasses import dataclass
from pathlib import Path


class WaveformError(Exception):
    """A bounded waveform projection could not be produced."""


@dataclass(frozen=True, slots=True)
class WaveformBucket:
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class WaveformWindow:
    asset_id: int
    media_path: Path
    start_ms: int
    end_ms: int
    buckets: tuple[WaveformBucket, ...]


def build_envelope(samples: list[float], bucket_count: int) -> tuple[WaveformBucket, ...]:
    """Reduce samples into min/max buckets without dropping short transients."""
    if bucket_count <= 0:
        raise WaveformError("Waveform bucket count must be positive.")
    if not samples:
        raise WaveformError("The requested media window contained no audio samples.")
    count = min(bucket_count, len(samples))
    result: list[WaveformBucket] = []
    for index in range(count):
        start = index * len(samples) // count
        end = (index + 1) * len(samples) // count
        bucket = samples[start:end]
        result.append(WaveformBucket(min(bucket), max(bucket)))
    return tuple(result)
