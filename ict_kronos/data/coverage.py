"""Why a resampled bar is missing source observations — and whether that matters.

Full semantics in ``docs/features/data_coverage.md``.

Fresh July-2026 real data exposed a defect the four-day fixture could never show. The
resampler kept a target bar only when it had a **full** complement of source bars —
1440 of 1440 one-minute bars for a Daily. Real FX never delivers that. The best EURUSD
day in July 2026 had 1438 minutes, missing 21:03 and 23:39. Measured loss:

.. code-block:: text

    5m   1.7%      15m  4.3%      1H  10.7%      4H  22.5%      1D  100%

The engine had been validated on the timeframes that lose almost nothing, while
production uses the three that lose the most.

**The rule was answering the wrong question.** "Did every constituent minute trade?"
and "is this bar a valid aggregation of what traded?" are different questions. A minute
with no ticks is a minute with no trades, not missing data.

So this module separates the causes rather than pooling them:

.. code-block:: text

    BOUNDARY        PROVEN     the bar's period is not fully inside the observed data
    MARKET_CLOSED   PROVEN     every missing observation falls in a recurring closure
    UNDETERMINED    NOT PROVEN retained, and flagged as such

**Only ``BOUNDARY`` rejects a bar.** A coverage ratio is a *quality signal*, never a
validity rule — there is no 95%, 98% or 99% threshold here, because no evidence
supports one. Nothing is ever fabricated, forward-filled or interpolated: a gap stays
a gap, and what changes is only whether the surrounding bar is thrown away.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from ..domain import Symbol, Timeframe


class GapCause(StrEnum):
    """Why source observations are absent from a target bar's period."""

    #: Nothing is missing.
    NONE = "none"
    #: PROVEN from the dataset extent: the bar's period is not fully observed.
    BOUNDARY = "boundary"
    #: PROVEN from the dataset's own recurring profile: the market was shut.
    MARKET_CLOSED = "market_closed"
    #: NOT proven either way. Retained, and honest about it.
    UNDETERMINED = "undetermined"


class BarQuality(StrEnum):
    """What a consumer should believe about a bar.

    Distinct from :class:`GapCause` on purpose: the cause is about the *data*, the
    quality is about *fitness for use*, and collapsing them would make "the market was
    closed" indistinguishable from "we do not know why this is missing".
    """

    #: Every expected observation is present.
    COMPLETE = "complete"
    #: Observations are missing, and all of them are in a proven market closure.
    MARKET_GAP = "market_gap"
    #: Observations are missing and the cause is not established. Usable, flagged.
    DEGRADED_UNKNOWN = "degraded_unknown"
    #: The period is not fully covered by the dataset. **Not production-eligible.**
    BOUNDARY_INCOMPLETE = "boundary_incomplete"


@dataclass(frozen=True)
class SessionProfile:
    """Recurring closures inferred from the dataset itself — never assumed.

    A ``(weekday, minute_of_day)`` slot is treated as closed when it is absent on
    **every** observed occurrence of that weekday. One tick, on one day, anywhere in
    the window, disqualifies it. That is the most conservative form of the rule: it can
    only ever under-claim closure, so a real provider outage is never quietly relabelled
    as "the market was shut".

    Per-weekday, because FX closes on Friday evening and reopens on Sunday evening — a
    minute that is closed on Friday is wide open on Tuesday.

    ``min_occurrences`` is a **sample-size guard, not a coverage threshold**: "recurring"
    cannot be concluded from a single observation of a weekday. Two is the smallest
    number for which the word means anything.

    This is an inference about *this dataset*, and it is labelled as one. It is not a
    holiday calendar and does not pretend to be; a real session/holiday calendar is a
    separate story and would supersede it.
    """

    symbol: str
    #: ``(weekday, minute_of_day)`` slots absent on every observed occurrence.
    closed_slots: frozenset[tuple[int, int]]
    #: How many times each weekday appears in the source data.
    weekday_occurrences: dict[int, int]
    min_occurrences: int = 2

    def is_closed(self, moment: pd.Timestamp) -> bool:
        return (moment.dayofweek, moment.hour * 60 + moment.minute) in self.closed_slots

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "closed_slot_count": len(self.closed_slots),
            "weekday_occurrences": dict(sorted(self.weekday_occurrences.items())),
            "min_occurrences": self.min_occurrences,
        }

    @classmethod
    def from_source(
        cls,
        frame: pd.DataFrame,
        source: Timeframe,
        symbol: Symbol,
        *,
        min_occurrences: int = 2,
    ) -> SessionProfile:
        """Infer recurring closures from observed bars. Pure function of the frame."""
        if len(frame) == 0:
            return cls(symbol=symbol.value, closed_slots=frozenset(), weekday_occurrences={})

        index = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], utc=True)).sort_values()
        grid = pd.date_range(index.min(), index.max(), freq=source.duration, tz="UTC")

        present = pd.DataFrame(
            {
                "weekday": grid.dayofweek,
                "minute": grid.hour * 60 + grid.minute,
                "date": grid.date,
                "seen": grid.isin(index),
            }
        )
        #: How many distinct calendar days each weekday contributes.
        occurrences = (
            present.groupby("weekday")["date"].nunique().to_dict()  # type: ignore[assignment]
        )

        by_slot = present.groupby(["weekday", "minute"])["seen"]
        never_seen = by_slot.sum() == 0
        counts = by_slot.count()

        closed = {
            (int(weekday), int(minute))
            for (weekday, minute), absent in never_seen.items()
            if absent and counts.loc[(weekday, minute)] >= min_occurrences
        }
        return cls(
            symbol=symbol.value,
            closed_slots=frozenset(closed),
            weekday_occurrences={int(k): int(v) for k, v in occurrences.items()},
            min_occurrences=min_occurrences,
        )


@dataclass(frozen=True)
class BarCoverage:
    """One target bar's coverage evidence. Everything a reviewer needs, nothing derived away."""

    timestamp: pd.Timestamp
    expected_source_observations: int
    actual_source_observations: int
    #: Missing observations that fall in a proven recurring closure.
    market_closed_observations: int
    #: Missing observations with no established cause.
    undetermined_observations: int
    #: Longest run of consecutive missing observations. A structured outage looks
    #: nothing like scattered quiet minutes, and pooling them would hide that.
    longest_missing_run: int
    boundary_incomplete: bool
    cause: GapCause
    quality: BarQuality

    @property
    def missing_observations(self) -> int:
        return self.expected_source_observations - self.actual_source_observations

    @property
    def coverage_ratio(self) -> float:
        """A QUALITY SIGNAL, not a validity rule. Nothing is rejected on this number."""
        if self.expected_source_observations == 0:
            return 0.0
        return self.actual_source_observations / self.expected_source_observations

    @property
    def production_eligible(self) -> bool:
        """Only a boundary-incomplete bar is refused — see the module docstring."""
        return not self.boundary_incomplete

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "expected_source_observations": self.expected_source_observations,
            "actual_source_observations": self.actual_source_observations,
            "missing_observations": self.missing_observations,
            "coverage_ratio": self.coverage_ratio,
            "market_closed_observations": self.market_closed_observations,
            "undetermined_observations": self.undetermined_observations,
            "longest_missing_run": self.longest_missing_run,
            "boundary_incomplete": self.boundary_incomplete,
            "cause": self.cause.value,
            "quality": self.quality.value,
            "production_eligible": self.production_eligible,
        }


@dataclass(frozen=True)
class CoverageReport:
    """Per-bar coverage for one (source → target) aggregation, plus its summary."""

    symbol: str
    source: str
    target: str
    profile: SessionProfile
    bars: tuple[BarCoverage, ...]

    def by_timestamp(self) -> dict[pd.Timestamp, BarCoverage]:
        return {bar.timestamp: bar for bar in self.bars}

    def counts(self) -> dict[str, int]:
        out = {quality.value: 0 for quality in BarQuality}
        for bar in self.bars:
            out[bar.quality.value] += 1
        return out

    def cause_counts(self) -> dict[str, int]:
        out = {cause.value: 0 for cause in GapCause}
        for bar in self.bars:
            out[bar.cause.value] += 1
        return out

    def rejected(self) -> tuple[BarCoverage, ...]:
        return tuple(bar for bar in self.bars if not bar.production_eligible)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "target": self.target,
            "profile": self.profile.as_dict(),
            "bar_count": len(self.bars),
            "quality_counts": self.counts(),
            "cause_counts": self.cause_counts(),
            "rejected_count": len(self.rejected()),
        }


def coverage_report(
    frame: pd.DataFrame,
    source: Timeframe,
    target: Timeframe,
    symbol: Symbol,
    *,
    profile: SessionProfile | None = None,
) -> CoverageReport:
    """Classify every target bar's missing observations. Pure; reads only ``frame``."""
    if len(frame) == 0:
        return CoverageReport(
            symbol=symbol.value,
            source=source.value,
            target=target.value,
            profile=profile or SessionProfile(symbol.value, frozenset(), {}),
            bars=(),
        )

    known = profile or SessionProfile.from_source(frame, source, symbol)
    index = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], utc=True)).sort_values()

    observed_start = index.min()
    #: The last instant the dataset can speak for: the final source bar's CLOSE.
    observed_end = index.max() + source.duration

    grid = pd.date_range(observed_start, index.max(), freq=source.duration, tz="UTC")
    present = pd.Series(grid.isin(index), index=grid)
    expected_per_bar = target.minutes // source.minutes

    bars: list[BarCoverage] = []
    for start, chunk in present.groupby(pd.Grouper(freq=target.pandas_freq)):
        if chunk.empty or not bool(chunk.any()):
            # No source bar in this period at all: the resampler emits nothing here,
            # so there is no target bar to describe.
            continue

        end = start + target.duration
        boundary = start < observed_start or end > observed_end

        missing = chunk.index[~chunk.to_numpy()]
        closed = sum(1 for moment in missing if known.is_closed(moment))
        undetermined = len(missing) - closed
        actual = int(chunk.sum())

        if boundary:
            cause, quality = GapCause.BOUNDARY, BarQuality.BOUNDARY_INCOMPLETE
        elif len(missing) == 0 and actual == expected_per_bar:
            cause, quality = GapCause.NONE, BarQuality.COMPLETE
        elif undetermined == 0:
            cause, quality = GapCause.MARKET_CLOSED, BarQuality.MARKET_GAP
        else:
            cause, quality = GapCause.UNDETERMINED, BarQuality.DEGRADED_UNKNOWN

        bars.append(
            BarCoverage(
                timestamp=start,
                expected_source_observations=expected_per_bar,
                actual_source_observations=actual,
                market_closed_observations=closed,
                undetermined_observations=undetermined,
                longest_missing_run=_longest_run(chunk.to_numpy()),
                boundary_incomplete=boundary,
                cause=cause,
                quality=quality,
            )
        )

    return CoverageReport(
        symbol=symbol.value,
        source=source.value,
        target=target.value,
        profile=known,
        bars=tuple(bars),
    )


def _longest_run(present) -> int:
    """Longest run of consecutive absent observations in one period."""
    longest = run = 0
    for seen in present:
        run = 0 if seen else run + 1
        longest = max(longest, run)
    return longest


__all__ = [
    "BarCoverage",
    "BarQuality",
    "CoverageReport",
    "GapCause",
    "SessionProfile",
    "coverage_report",
]
