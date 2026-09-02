"""Transient pedagogical boundary state used by the review UI."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BoundaryInterval:
    start_ms: int
    end_ms: int


class ProvisionalBoundaryModel:
    """Edit a transient interval while retaining immutable MFA evidence."""

    def __init__(
        self,
        *,
        speech_start_ms: int,
        speech_end_ms: int,
        visible_start_ms: int,
        visible_end_ms: int,
        preceding_silence_start_ms: int | None = None,
        following_silence_end_ms: int | None = None,
    ) -> None:
        if not 0 <= visible_start_ms <= speech_start_ms < speech_end_ms <= visible_end_ms:
            raise ValueError("Speech must be a non-empty interval inside the visible window.")
        self.speech = BoundaryInterval(speech_start_ms, speech_end_ms)
        self.visible = BoundaryInterval(visible_start_ms, visible_end_ms)
        default_start = max(visible_start_ms, 0, speech_start_ms - 50)
        default_end = min(visible_end_ms, speech_end_ms + 150)
        if preceding_silence_start_ms is not None:
            default_start = max(default_start, preceding_silence_start_ms)
        if following_silence_end_ms is not None:
            default_end = min(default_end, following_silence_end_ms)
        self.default = BoundaryInterval(default_start, default_end)
        self._start_ms = default_start
        self._end_ms = default_end

    @property
    def current(self) -> BoundaryInterval:
        return BoundaryInterval(self._start_ms, self._end_ms)

    def set_start(self, milliseconds: int) -> BoundaryInterval:
        self._start_ms = max(
            self.visible.start_ms, min(int(milliseconds), self._end_ms - 1)
        )
        return self.current

    def set_end(self, milliseconds: int) -> BoundaryInterval:
        self._end_ms = min(
            self.visible.end_ms, max(int(milliseconds), self._start_ms + 1)
        )
        return self.current

    def nudge_start(self, delta_ms: int) -> BoundaryInterval:
        return self.set_start(self._start_ms + delta_ms)

    def nudge_end(self, delta_ms: int) -> BoundaryInterval:
        return self.set_end(self._end_ms + delta_ms)

    def reset(self) -> BoundaryInterval:
        self._start_ms = self.default.start_ms
        self._end_ms = self.default.end_ms
        return self.current

