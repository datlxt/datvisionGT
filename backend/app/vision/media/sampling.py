from fractions import Fraction


class TimestampSampler:
    """Select the first decoded frame at or after each target presentation time."""

    def __init__(self, sample_rate: float) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero")
        self.interval_us = Fraction(1_000_000, 1) / Fraction(str(sample_rate))
        self.next_target_us = Fraction(0, 1)

    def select(self, timestamp_us: int) -> int | None:
        if timestamp_us < self.next_target_us:
            return None

        selected_target = self.next_target_us
        while self.next_target_us <= timestamp_us:
            self.next_target_us += self.interval_us
        return round(selected_target)


class FrameTimingAnalyzer:
    """Classify VFR from decoded presentation-time deltas using a small tolerance."""

    def __init__(self, tolerance_us: int = 1_000) -> None:
        self.tolerance_us = tolerance_us
        self._previous: int | None = None
        self._deltas: list[int] = []

    def observe(self, timestamp_us: int) -> None:
        if self._previous is not None:
            delta = timestamp_us - self._previous
            if delta > 0:
                self._deltas.append(delta)
        self._previous = timestamp_us

    @property
    def representative_deltas_us(self) -> tuple[int, ...]:
        return tuple(sorted(set(self._deltas))[:20])

    @property
    def is_variable_frame_rate(self) -> bool | None:
        if len(self._deltas) < 2:
            return None
        ordered = sorted(self._deltas)
        median = ordered[len(ordered) // 2]
        return any(abs(delta - median) > self.tolerance_us for delta in ordered)
