"""Timeframe — the canonical bar-interval vocabulary.

Ordering matters: multi-timeframe alignment (Phase 3) needs to know that 1H is
"higher" than 5M so it can expose HTF context to LTF observations, and the
resampler needs to know which aggregations are legal.

CLAUDE.md timestamp convention: a bar's timestamp is its OPEN time, and the bar
covers ``[timestamp, timestamp + duration)``.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum


class Timeframe(StrEnum):
    """Supported bar intervals, ordered from fastest to slowest.

    Values are the canonical lowercase strings used in file paths, config and
    the job outbox contract. They are STRINGS, not ints — a deliberate
    cross-process contract, ported from Learnexia's PipelineJob convention.
    """

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def minutes(self) -> int:
        """Bar duration in whole minutes."""
        return _MINUTES[self]

    @property
    def duration(self) -> timedelta:
        return timedelta(minutes=self.minutes)

    @property
    def pandas_freq(self) -> str:
        """Pandas resample rule for this timeframe.

        Uses ``min``/``h``/``D`` (the non-deprecated aliases in pandas >= 2.2).
        """
        return _PANDAS_FREQ[self]

    def is_higher_than(self, other: Timeframe) -> bool:
        return self.minutes > other.minutes

    def can_aggregate_from(self, source: Timeframe) -> bool:
        """True if bars of ``source`` can be aggregated into bars of ``self``.

        Requires the source to be strictly faster AND to divide this timeframe
        evenly — otherwise the aggregation would straddle bar boundaries and
        produce silently wrong opens/closes.
        """
        return source.minutes < self.minutes and self.minutes % source.minutes == 0

    @classmethod
    def from_string(cls, raw: str) -> Timeframe:
        """Parse a timeframe string, case- and whitespace-insensitively."""
        key = raw.strip().lower()
        try:
            return cls(key)
        except ValueError as exc:
            valid = ", ".join(t.value for t in cls)
            raise ValueError(f"unknown timeframe {raw!r}; expected one of: {valid}") from exc


# D1 is treated as 1440 minutes. This is the *bar* duration, not a claim about
# FX session length — daily-bar boundary policy is a separate, configurable
# concern handled by the session layer in Phase 2.
_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}

_PANDAS_FREQ: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1D",
}
